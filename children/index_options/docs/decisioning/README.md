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
| A0053 | execute | staged approval | 2026-09-24T02:25:28+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-3aa2213a/stages/approval.json |  | stage_token=3aa2213a1551dbb56e45007a9ecc19344452f8b9bbf3d0395e26ead1896b8657:approval; state=ran; sha256=f1c472b2ca24a361906928f94c4f7f4e763fb78b18736bf002b7e29823561501; reason= |
| A0054 | execute | index-options-real-distribution walk-forward | 2026-09-24T02:25:55+00:00 | index-options-real-distribution | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-03505e18 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-03505e18 | state=ran folds=32 hash=03505e18 asof=2026-09-23 |
| A0055 | execute | index-options-real-har walk-forward | 2026-09-24T02:26:24+00:00 | index-options-real-har | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-walkforward-2026-09-23-1a82ece5 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-walkforward-2026-09-23-1a82ece5 | state=ran folds=32 hash=1a82ece5 asof=2026-09-23 |
| A0056 | execute | index-options-real-lightgbm walk-forward | 2026-09-24T02:29:17+00:00 | index-options-real-lightgbm | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-walkforward-2026-09-23-66fc5574 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-walkforward-2026-09-23-66fc5574 | state=ran folds=32 hash=66fc5574 asof=2026-09-23 |
| A0057 | execute | index-options-real-vix walk-forward | 2026-09-24T02:29:48+00:00 | index-options-real-vix | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-vix-walkforward-2026-09-23-8d35a43d | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-vix-walkforward-2026-09-23-8d35a43d | state=ran folds=32 hash=8d35a43d asof=2026-09-23 |
| A0058 | execute | index-options-real-har-vix walk-forward | 2026-09-24T02:30:18+00:00 | index-options-real-har-vix | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-vix-walkforward-2026-09-23-0f5db0ca | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-vix-walkforward-2026-09-23-0f5db0ca | state=ran folds=32 hash=0f5db0ca asof=2026-09-23 |
| A0059 | execute | index-options-real-lightgbm-vix walk-forward | 2026-09-24T02:34:40+00:00 | index-options-real-lightgbm-vix | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-vix-walkforward-2026-09-23-c6e98bd3 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-vix-walkforward-2026-09-23-c6e98bd3 | state=ran folds=32 hash=c6e98bd3 asof=2026-09-23 |
| A0060 | execute | staged run | 2026-09-24T02:34:40+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-3aa2213a/stages/run.json |  | stage_token=3aa2213a1551dbb56e45007a9ecc19344452f8b9bbf3d0395e26ead1896b8657:run; state=ran; sha256=9efd43b165bb99496b922443c8fedc866356012f279f185b984ac397d278fc41; reason= |
| A0061 | execute | staged compare | 2026-09-24T02:34:40+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-3aa2213a/stages/compare.json |  | stage_token=3aa2213a1551dbb56e45007a9ecc19344452f8b9bbf3d0395e26ead1896b8657:compare; state=ran; sha256=b4847a4de625db294c1159d00f49224d81344598b63956d931760da384384321; reason= |
| A0062 | research | real-data-backtest/2026-09-24-zoo-vs-vix | 2026-09-24T02:35:06+00:00 | Real-data zoo and condor backtest vs VIX | docs/research/real-data-backtest/2026-09-24-zoo-vs-vix.md | docs/research/real-data-backtest/2026-09-24-zoo-vs-vix.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
