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
| A1307 | acquire | register-source theta-own-iv-index-daily | 2026-10-27T13:30:13+00:00 | --root /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob --source  --stream  --mode | b090e40de94ad079ff662c760286371aaeeb1f9e5e1f2ee20be35cc604a65af8 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob | connector=localtables |
| A1308 | acquire | backfill theta-own-iv-index-daily/index_daily | 2026-10-27T13:30:15+00:00 | --root /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob --source theta-own-iv-index-daily --stream index_daily --mode backfill | 1ad541072bf18d1f9af9db9816d98fa68daef63cc71efd905000e54dba50a56b | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob |  |
| A1309 | acquire | register-source theta-raw-chain-features | 2026-10-27T13:32:45+00:00 | --root /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob --source  --stream  --mode | 61dc69e3a897b5ffeea937cd2956b7ee6c46fd90af6d3d4b2b6ff8598a96d529 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob | connector=localblobs |
| A1310 | acquire | backfill theta-raw-chain-features/files | 2026-10-27T13:32:45+00:00 | --root /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob --source theta-raw-chain-features --stream files --mode backfill | d52b6b7618d384a6898c96118ae6c92d23615975e2773c5a7f55a4457e257004 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/ob |  |
| A1311 | execute | strike-region-scores | 2026-10-27T14:05:03+00:00 | strike-region-scores | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-strike-region-scores-wide-mlp64-s29/strike-region-scores-2019-12-31-458f39b4 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-strike-region-scores-wide-mlp64-s29/strike-region-scores-2019-12-31-458f39b4 | state=ran hash=458f39b4 asof=2019-12-31 |
| A1312 | execute | edge-method-48-wide-mlp64 | 2026-10-27T14:06:03+00:00 | edge-method-48-wide-mlp64 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m48/edge-method-48-wide-mlp64-2019-12-31-c199fa97 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m48/edge-method-48-wide-mlp64-2019-12-31-c199fa97 | state=ran hash=c199fa97 asof=2019-12-31 |
| A1313 | execute | edge-method-51-qa-mlp64-by-dispersion | 2026-10-27T14:06:31+00:00 | edge-method-51-qa-mlp64-by-dispersion | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m51/edge-method-51-qa-mlp64-by-dispersion-2019-12-31-4138329c | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m51/edge-method-51-qa-mlp64-by-dispersion-2019-12-31-4138329c | state=ran hash=4138329c asof=2019-12-31 |
| A1314 | execute | edge-method-53-qa-mlp64-rolling-by-dispersion | 2026-10-27T14:08:53+00:00 | edge-method-53-qa-mlp64-rolling-by-dispersion | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m53/edge-method-53-qa-mlp64-rolling-by-dispersion-2019-12-31-ce3cb516 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m53/edge-method-53-qa-mlp64-rolling-by-dispersion-2019-12-31-ce3cb516 | state=ran hash=ce3cb516 asof=2019-12-31 |
| A1315 | execute | edge-method-58-qa-mlp64-by-market-width | 2026-10-27T14:09:09+00:00 | edge-method-58-qa-mlp64-by-market-width | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m58/edge-method-58-qa-mlp64-by-market-width-2019-12-31-2fb4278e | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m58/edge-method-58-qa-mlp64-by-market-width-2019-12-31-2fb4278e | state=ran hash=2fb4278e asof=2019-12-31 |
| A1316 | execute | theta-broad-2019-model-only-baseline | 2026-10-27T14:13:12+00:00 | theta-broad-2019-model-only-baseline | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-model-only/theta-broad-2019-model-only-baseline-2019-12-31-fc5fe688 | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-model-only/theta-broad-2019-model-only-baseline-2019-12-31-fc5fe688 | state=ran hash=fc5fe688 asof=2019-12-31 |

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
| A0008 | Training sequence and run | Retrain periodically for all events: warm-up 2017-2018 validated on 2019 (never ranked), then yearly expanding retrains (through 2019 to 2020, through 2020 to 2021, through 2021 to 2022, through 2022 to 2023). The run is TCN, chain arm (best of the first five), one seed (11), one model judged alone, no averaging. Supersedes A0004's screen rung | children/index_options/configs/strike-screen-folds-walkforward.jsonl | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
| A1314 | 36 broad M53 ticker-DTE research candidates | Owner-selected provisional set: 36 ticker/calendar-DTE bands with at least 50 comparable rows in each 2019 half and lower M53 twCRPS than raw market, calibrated market, and per-name empirical reference in both. Continue testing all 36; standalone-model edge is not required. This is not A0005/A0007 eligibility and does not change A0003/A0008 windows or open holdout. | children/index_options/docs/memos/2026-10-27-2019-h1-h2-ticker-dte-performance.csv; children/index_options/docs/memos/2026-10-27-2019-h1-h2-ticker-dte-performance.md; children/index_options/docs/memos/2026-10-27-2019-h2-matched-universe-comparison.md | N |  | execute | edge-method-53-qa-mlp64-rolling-by-dispersion | judgemental | /home/russell/data/index_options/theta-broad-2019-zoo-20261027/scores/run-m53/edge-method-53-qa-mlp64-rolling-by-dispersion-2019-12-31-ce3cb516 |
