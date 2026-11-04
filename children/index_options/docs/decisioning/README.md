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
| A1338 | execute | m53-memory-namespace-correction | 2026-11-04T00:02:16+00:00 | ADR-0291; independent correctness/integration reviews of4a38e97e;25 RED refusal cases | Complete output namespace guard and Path staging correction; frozen eight hypotheses unchanged; fresh review required | /home/russell/data/index_options/m53-error-memory-study-20261103 |  |
| A1339 | research | distribution-modeling/2026-11-04-m53-error-memory-study | 2026-11-04T00:32:51+00:00 | Eight M53 error corrections fail the continuation test | docs/research/distribution-modeling/2026-11-04-m53-error-memory-study.md | docs/research/distribution-modeling/2026-11-04-m53-error-memory-study.md |  |
| A1340 | research | distribution-modeling/2026-11-04-m53-no-mlp-study | 2026-11-04T01:31:14+00:00 | M53 loses a small edge without its MLP | docs/research/distribution-modeling/2026-11-04-m53-no-mlp-study.md | docs/research/distribution-modeling/2026-11-04-m53-no-mlp-study.md |  |
| A1341 | research | distribution-modeling/2026-11-04-m53-initialization-study | 2026-11-04T04:05:17+00:00 | M53 training improves on initialization | docs/research/distribution-modeling/2026-11-04-m53-initialization-study.md | docs/research/distribution-modeling/2026-11-04-m53-initialization-study.md |  |
| A1342 | research | distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio | 2026-11-04T11:24:23+00:00 | Blinded quarterly90 forecasts and bounded quarterly MIO | docs/research/distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio.md | docs/research/distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio.md |  |
| A1343 | research | blinded-quarterly90-delivery-evidence | 2026-11-04T11:27:23+00:00 | Retained review coverage and readable final evidence | docs/research/distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio.md | docs/research/distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio.md |  |
| A1344 | research | distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio | 2026-11-04T14:54:14+00:00 | Quarterly M53 comparison and 2019 MIO limits | docs/research/distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio.md | docs/research/distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio.md |  |
| A1345 | research | quarterly-comparison-coverage-correction | 2026-11-04T15:01:08+00:00 | Spacing clarification in quarterly comparison evidence | docs/research/distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio.md | docs/research/distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio.md |  |
| A1346 | research | quarterly-comparison-coverage-correction | 2026-11-04T15:01:08+00:00 | Additive correction to blinded delivery MIO coverage | docs/research/distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio.md | docs/research/distribution-modeling/2026-11-04-blinded-quarterly90-cache-and-mio.md |  |
| A1347 | research | lly-recovery-wsl-mac-verification | 2026-11-04T16:47:43+00:00 | Owner-authorized source recovery and independent Mac structural verification | docs/research/distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio.md | /home/russell/data/index_options/quarterly-prediction-cache-20261104/2019-extension/lly-recovery-20261104 | 4084 records on 5 of 134 sessions; 129 missing. No new fit/MIO, no coverage change; failed attempts preserved. |

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
