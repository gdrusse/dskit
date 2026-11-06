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
| A1380 | execute | quarterly-scores-2021q3 | 2026-11-06T01:47:37+00:00 | quarterly-scores-2021q3 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2021Q3/quarterly-scores-2021q3-2023-12-31-e7466420 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2021Q3/quarterly-scores-2021q3-2023-12-31-e7466420 | state=ran hash=e7466420 asof=2023-12-31 |
| A1381 | execute | quarterly-scores-2021q4 | 2026-11-06T01:49:39+00:00 | quarterly-scores-2021q4 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2021Q4/quarterly-scores-2021q4-2023-12-31-51beb33d | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2021Q4/quarterly-scores-2021q4-2023-12-31-51beb33d | state=ran hash=51beb33d asof=2023-12-31 |
| A1382 | execute | quarterly-scores-2022q1 | 2026-11-06T01:54:14+00:00 | quarterly-scores-2022q1 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q1/quarterly-scores-2022q1-2023-12-31-5fd79e73 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q1/quarterly-scores-2022q1-2023-12-31-5fd79e73 | state=ran hash=5fd79e73 asof=2023-12-31 |
| A1383 | execute | quarterly-scores-2022q2 | 2026-11-06T01:56:29+00:00 | quarterly-scores-2022q2 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q2/quarterly-scores-2022q2-2023-12-31-1233f491 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q2/quarterly-scores-2022q2-2023-12-31-1233f491 | state=ran hash=1233f491 asof=2023-12-31 |
| A1384 | execute | quarterly-scores-2022q3 | 2026-11-06T02:01:20+00:00 | quarterly-scores-2022q3 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q3/quarterly-scores-2022q3-2023-12-31-ef922a34 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q3/quarterly-scores-2022q3-2023-12-31-ef922a34 | state=ran hash=ef922a34 asof=2023-12-31 |
| A1385 | execute | quarterly-scores-2022q4 | 2026-11-06T02:03:23+00:00 | quarterly-scores-2022q4 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q4/quarterly-scores-2022q4-2023-12-31-a2bcfc16 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2022Q4/quarterly-scores-2022q4-2023-12-31-a2bcfc16 | state=ran hash=a2bcfc16 asof=2023-12-31 |
| A1386 | execute | quarterly-scores-2023q1 | 2026-11-06T02:07:57+00:00 | quarterly-scores-2023q1 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q1/quarterly-scores-2023q1-2023-12-31-936ab9ed | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q1/quarterly-scores-2023q1-2023-12-31-936ab9ed | state=ran hash=936ab9ed asof=2023-12-31 |
| A1387 | execute | quarterly-scores-2023q2 | 2026-11-06T02:10:28+00:00 | quarterly-scores-2023q2 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q2/quarterly-scores-2023q2-2023-12-31-f08ed857 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q2/quarterly-scores-2023q2-2023-12-31-f08ed857 | state=ran hash=f08ed857 asof=2023-12-31 |
| A1388 | execute | quarterly-scores-2023q3 | 2026-11-06T02:14:30+00:00 | quarterly-scores-2023q3 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q3/quarterly-scores-2023q3-2023-12-31-de67ecea | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q3/quarterly-scores-2023q3-2023-12-31-de67ecea | state=ran hash=de67ecea asof=2023-12-31 |
| A1389 | execute | quarterly-scores-2023q4 | 2026-11-06T02:17:03+00:00 | quarterly-scores-2023q4 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q4/quarterly-scores-2023q4-2023-12-31-5db1a5a1 | /mnt/d/full-span-rerun-20261105/quarterly/scores/runs/2023Q4/quarterly-scores-2023q4-2023-12-31-5db1a5a1 | state=ran hash=5db1a5a1 asof=2023-12-31 |

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
| A0009 | Locked ticker list | The one ticker list for every new fit, forecast and MIO run: 166 names, SHA-256 8eae0a07fe2bc667e597476f00909359d12c57c6d180f3f2d954e1b907fbc90e. Rule uses dates and input presence only (full span 2017-01-03..2025-12-31, at most 5 sessions missing, complete bars and liquidity, identity check, at least 40 market-eligible rows in 2017-2019). Any change needs an owner decision and a logged reason (owner 2026-11-04: lock the ticker list so that this does not happen again; only full-span tickers). Survival bias remains and is unmeasured | children/index_options/configs/locked-ticker-list.json;children/index_options/configs/locked-ticker-list-exclusions.csv;children/index_options/index_options/ticker_lock.py | Y |  | acquire | live cboe-chain/option_chain | judgemental | /home/russell/data/index_options/ob |
