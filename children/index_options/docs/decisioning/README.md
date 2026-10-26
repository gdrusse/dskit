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
| A1289 | execute | edge-method-63-qa-mlp64-rn-shape-weights | 2026-10-21T19:45:27+00:00 | edge-method-63-qa-mlp64-rn-shape-weights | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-63-qa-mlp64-rn-shape-weights/edge-method-63-qa-mlp64-rn-shape-weights-2023-12-31-7b924934 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-63-qa-mlp64-rn-shape-weights/edge-method-63-qa-mlp64-rn-shape-weights-2023-12-31-7b924934 | state=ran hash=7b924934 asof=2023-12-31 |
| A1290 | execute | strike-region-scores | 2026-10-21T20:40:56+00:00 | strike-region-scores | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-wide-mlp64-s29/strike-region-scores-2023-12-31-8e7a4a59 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-wide-mlp64-s29/strike-region-scores-2023-12-31-8e7a4a59 | state=ran hash=8e7a4a59 asof=2023-12-31 |
| A1291 | execute | strike-region-scores | 2026-10-21T21:18:27+00:00 | strike-region-scores | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-wide-mlp64-s47/strike-region-scores-2023-12-31-d11e8002 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-wide-mlp64-s47/strike-region-scores-2023-12-31-d11e8002 | state=ran hash=d11e8002 asof=2023-12-31 |
| A1292 | execute | edge-method-57a-mlp64-seed-bag | 2026-10-21T21:20:28+00:00 | edge-method-57a-mlp64-seed-bag | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-57a-mlp64-seed-bag/edge-method-57a-mlp64-seed-bag-2023-12-31-b9c9f6c4 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-57a-mlp64-seed-bag/edge-method-57a-mlp64-seed-bag-2023-12-31-b9c9f6c4 | state=ran hash=b9c9f6c4 asof=2023-12-31 |
| A1293 | execute | edge-method-57-qa-mlp64-seed-bag | 2026-10-22T21:24:52+00:00 | edge-method-57-qa-mlp64-seed-bag | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-57-qa-mlp64-seed-bag/edge-method-57-qa-mlp64-seed-bag-2023-12-31-7b704c9a | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-57-qa-mlp64-seed-bag/edge-method-57-qa-mlp64-seed-bag-2023-12-31-7b704c9a | state=ran hash=7b704c9a asof=2023-12-31 |
| A1294 | execute | strike-region-scores | 2026-10-22T22:05:18+00:00 | strike-region-scores | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-mlp64-d03/strike-region-scores-2023-12-31-e6752600 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-mlp64-d03/strike-region-scores-2023-12-31-e6752600 | state=ran hash=e6752600 asof=2023-12-31 |
| A1295 | execute | edge-method-64-mlp64-66-inputs | 2026-10-22T22:11:28+00:00 | edge-method-64-mlp64-66-inputs | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-64-mlp64-66-inputs/edge-method-64-mlp64-66-inputs-2023-12-31-46befb34 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-64-mlp64-66-inputs/edge-method-64-mlp64-66-inputs-2023-12-31-46befb34 | state=ran hash=46befb34 asof=2023-12-31 |
| A1296 | execute | strike-region-scores | 2026-10-22T22:52:13+00:00 | strike-region-scores | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-wider-mlp64/strike-region-scores-2023-12-31-05c739d0 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-wf-wider-mlp64/strike-region-scores-2023-12-31-05c739d0 | state=ran hash=05c739d0 asof=2023-12-31 |
| A1297 | execute | edge-method-65-wider-mlp64 | 2026-10-22T22:54:57+00:00 | edge-method-65-wider-mlp64 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-65-wider-mlp64/edge-method-65-wider-mlp64-2023-12-31-acdec4e6 | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-edge-method-65-wider-mlp64/edge-method-65-wider-mlp64-2023-12-31-acdec4e6 | state=ran hash=acdec4e6 asof=2023-12-31 |
| A1298 | research | scenario-stress/2026-10-26-option-book-repricing | 2026-10-26T20:14:57+00:00 | JSON option-book scenario repricing | docs/research/scenario-stress/2026-10-26-option-book-repricing.md | docs/research/scenario-stress/2026-10-26-option-book-repricing.md |  |

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
