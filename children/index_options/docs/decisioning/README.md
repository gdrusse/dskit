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
| A1357 | acquire | register-source theta-prices-index-daily | 2026-11-05T03:28:39+00:00 | --root /dev/shm/full-span-audit/dry/ob --source  --stream  --mode | 53e05339b0066301d7bc12034428208f81207b81ba830a46b9c418c746f890c8 | /dev/shm/full-span-audit/dry/ob | connector=localtables |
| A1358 | acquire | backfill theta-prices-index-daily/index_daily | 2026-11-05T03:28:40+00:00 | --root /dev/shm/full-span-audit/dry/ob --source theta-prices-index-daily --stream index_daily --mode backfill | 76227dd6a8cc058530f7c0c6474f47cba9dab952a5466b3ee01c8b70fb7687b2 | /dev/shm/full-span-audit/dry/ob |  |
| A1359 | acquire | register-source theta-own-iv-index-daily | 2026-11-05T03:28:40+00:00 | --root /dev/shm/full-span-audit/dry/ob --source  --stream  --mode | b7a7f2bac0ac72345e88bd12d36c2734b4cb6dc24280f7f76bb17821aa9e797c | /dev/shm/full-span-audit/dry/ob | connector=localtables |
| A1360 | acquire | backfill theta-own-iv-index-daily/index_daily | 2026-11-05T03:28:40+00:00 | --root /dev/shm/full-span-audit/dry/ob --source theta-own-iv-index-daily --stream index_daily --mode backfill | 62377360b329d47c7d16566bd1896f6628cd19df30ab0b9ac113ae1917a28d14 | /dev/shm/full-span-audit/dry/ob |  |
| A1361 | acquire | register-source theta-raw-chain-features | 2026-11-05T03:28:53+00:00 | --root /dev/shm/full-span-audit/dry/ob --source  --stream  --mode | 87cf84e30f6e7970a6a8bb6c19331852b2a5447b6b6e41dade870f624db5d5ba | /dev/shm/full-span-audit/dry/ob | connector=localblobs |
| A1362 | acquire | backfill theta-raw-chain-features/files | 2026-11-05T03:28:53+00:00 | --root /dev/shm/full-span-audit/dry/ob --source theta-raw-chain-features --stream files --mode backfill | 021f8430c75f4d835661a7684dba097a222ee5312801541f49583e073f7428b2 | /dev/shm/full-span-audit/dry/ob |  |
| A1363 | execute | audit-b01 | 2026-11-05T04:11:48+00:00 | audit-b01 | /dev/shm/full-span-audit/b01/run/audit-b01-2023-12-31-312fe6fd | /dev/shm/full-span-audit/b01/run/audit-b01-2023-12-31-312fe6fd | state=ran hash=312fe6fd asof=2023-12-31 |
| A1364 | execute | audit-b02 | 2026-11-05T04:13:47+00:00 | audit-b02 | /dev/shm/full-span-audit/b02/run/audit-b02-2023-12-31-f4b5c168 | /dev/shm/full-span-audit/b02/run/audit-b02-2023-12-31-f4b5c168 | state=ran hash=f4b5c168 asof=2023-12-31 |
| A1365 | execute | audit-b03 | 2026-11-05T04:15:03+00:00 | audit-b03 | /dev/shm/full-span-audit/b03/run/audit-b03-2023-12-31-49434089 | /dev/shm/full-span-audit/b03/run/audit-b03-2023-12-31-49434089 | state=ran hash=49434089 asof=2023-12-31 |
| A1366 | execute | diag-nee | 2026-11-05T05:03:53+00:00 | diag-nee | /dev/shm/nee-diag/run/diag-nee-2023-12-31-fd0a0c46 | /dev/shm/nee-diag/run/diag-nee-2023-12-31-fd0a0c46 | state=ran hash=fd0a0c46 asof=2023-12-31 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Primary score: threshold-weighted CRPS with weight 1 on \|z\| in [0.5, 2.5] tapering to 0.25 at z=0 and \|z\|>=3, with full-distribution CRPS beside it; Brier and reliability at the listed strikes; PIT/Berkowitz in the tails; only proper weighted scores; never choose the evaluation subset by outcomes. Strike regions are a pooled decomposition, never a cell axis. Predictability only: no P&L or strategy economics | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | Y |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
| A0003 | Procedural windows | Development 2017-01-03..2019-12-31 (fit plus last cal_n origins as early-stop band); validation 2020-01-02..2023-12-31 reported also by sub-period; holdout 2024-01-02..2025-11-28 sealed and opened once for promoted recipes; 2026 sealed forever; a row is on the earlier side of any boundary only if its label settles before it (label_reaches) | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | research | distribution-modeling/2026-09-23-model-ladder | judgemental | docs/research/distribution-modeling/2026-09-23-model-ladder.md |
| A0004 | Fold rungs | Screen: warm-up fold (train 2017-2018, validate 2019; timing and learning curves only, never ranked) plus one scored fit trained through 2019 and scored 2020-2023. Promotion: quarterly expanding refits for each quarter of 2020-2023 with seeds 11/29/47. Holdout: quarterly expanding refits for each quarter of 2024-2025 on the same recipe, one opening (owner decision 2026-11-04, replacing yearly refits and the single fit through 2023) | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | research | distribution-modeling/2026-09-23-ml-dl-transformers | judgemental | docs/research/distribution-modeling/2026-09-23-ml-dl-transformers.md |
| A0005 | Promotion rule and uncertainty | Eligible only if paired CRPS skill vs horizon_empirical and vs the market-implied reference is above zero on the full validation window and in at least 3 of 4 years; two-way cluster-robust intervals (expiry and symbol); never the best cell by score | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | acquire | register-source cboe-index | judgemental | /home/russell/data/index_options/ob |
| A0006 | Cell definition | Cell = (ticker, expiry rank). Weekly names: ranks 1-8, rank k = the k-th listed Friday expiry (the 7-day windows 1-7, 8-14, ...). Monthly-only names: expiry position 1-3, per ticker but flagged underpowered, with a pooled monthly line beside them. Thursday holiday expiries stay | children/index_options/docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md | Y |  | acquire | register-source cboe-chain | judgemental | /home/russell/data/index_options/ob |
| A0007 | Eligibility gate | Simple: a cell passes if the model beats the raw market-implied distribution AND the per-name horizon baseline on the A0002 score in at least 3 of 4 yearly folds and at least 3 of 5 regimes (S0 pre-crash, S1 covid crash, S2 recovery, S3 rate shock, S4 2023). No multiplicity correction yet: report the number passing next to the number expected by luck. The recalibrated market and the pooled baseline join when built | children/index_options/docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
| A0008 | Training sequence and run | Retrain periodically for all events: warm-up 2017-2018 validated on 2019 (never ranked), refit at fixed epoch 1 for every seed (owner decision 2026-11-04), then quarterly expanding retrains: each quarter of 2020-2023 trains on all mature history before that quarter starts, with weights fixed within the quarter (owner decision 2026-11-04, replacing yearly retrains). Locked recipe (owner 2026-11-04): M53 with four blend components (MLP, market_cal, pooled, baseline) and causal quarterly weights; 90-input MLP at fixed epoch 1 with no per-fit epoch selection; model slot is the quantile average of seeds 11/29/47. Forecast caches for this exact recipe are not yet produced; exact output hash to be pinned when they are. Supersedes A0004's screen rung. Owner correction 2026-10-27 replaced the fixed TCN recipe with the best model-zoo output; this locked recipe is that selection. | children/index_options/configs/strike-screen-folds-walkforward.jsonl;children/index_options/docs/research/distribution-modeling/2026-11-05-m53-predictive-lock.md | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
