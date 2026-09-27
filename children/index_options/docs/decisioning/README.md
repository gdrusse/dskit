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
| A0490 | execute | index-options-grid-iwm-5-empirical-downside-roi walk-forward | 2026-09-27T21:35:37+00:00 | index-options-grid-iwm-5-empirical-downside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-5-empirical-downside-roi-walkforward-2026-09-27-161c4f10 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-5-empirical-downside-roi-walkforward-2026-09-27-161c4f10 | state=ran folds=18 hash=161c4f10 asof=2026-09-27 |
| A0491 | execute | index-options-grid-qqq-7-10-empirical-downside-roi walk-forward | 2026-09-27T21:35:49+00:00 | index-options-grid-qqq-7-10-empirical-downside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-qqq-7-10-empirical-downside-roi-walkforward-2026-09-27-9d27c913 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-qqq-7-10-empirical-downside-roi-walkforward-2026-09-27-9d27c913 | state=ran folds=15 hash=9d27c913 asof=2026-09-27 |
| A0492 | execute | index-options-grid-spy-5-empirical-downside-roi walk-forward | 2026-09-27T21:35:52+00:00 | index-options-grid-spy-5-empirical-downside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-5-empirical-downside-roi-walkforward-2026-09-27-c138a3d6 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-5-empirical-downside-roi-walkforward-2026-09-27-c138a3d6 | state=ran folds=18 hash=c138a3d6 asof=2026-09-27 |
| A0493 | execute | index-options-grid-iwm-5-empirical-upside-roi walk-forward | 2026-09-27T21:35:52+00:00 | index-options-grid-iwm-5-empirical-upside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-5-empirical-upside-roi-walkforward-2026-09-27-b6f3c4c4 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-5-empirical-upside-roi-walkforward-2026-09-27-b6f3c4c4 | state=ran folds=18 hash=b6f3c4c4 asof=2026-09-27 |
| A0494 | execute | index-options-grid-qqq-7-10-empirical-upside-roi walk-forward | 2026-09-27T21:35:59+00:00 | index-options-grid-qqq-7-10-empirical-upside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-qqq-7-10-empirical-upside-roi-walkforward-2026-09-27-16d70ca7 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-qqq-7-10-empirical-upside-roi-walkforward-2026-09-27-16d70ca7 | state=ran folds=15 hash=16d70ca7 asof=2026-09-27 |
| A0495 | execute | index-options-grid-iwm-7-10-empirical-downside-roi walk-forward | 2026-09-27T21:36:03+00:00 | index-options-grid-iwm-7-10-empirical-downside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-7-10-empirical-downside-roi-walkforward-2026-09-27-9dffc247 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-7-10-empirical-downside-roi-walkforward-2026-09-27-9dffc247 | state=ran folds=18 hash=9dffc247 asof=2026-09-27 |
| A0496 | execute | index-options-grid-spy-5-empirical-upside-roi walk-forward | 2026-09-27T21:36:04+00:00 | index-options-grid-spy-5-empirical-upside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-5-empirical-upside-roi-walkforward-2026-09-27-e5615ea8 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-5-empirical-upside-roi-walkforward-2026-09-27-e5615ea8 | state=ran folds=18 hash=e5615ea8 asof=2026-09-27 |
| A0497 | execute | index-options-grid-iwm-7-10-empirical-upside-roi walk-forward | 2026-09-27T21:36:18+00:00 | index-options-grid-iwm-7-10-empirical-upside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-7-10-empirical-upside-roi-walkforward-2026-09-27-ddf1b05d | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-iwm-7-10-empirical-upside-roi-walkforward-2026-09-27-ddf1b05d | state=ran folds=18 hash=ddf1b05d asof=2026-09-27 |
| A0498 | execute | index-options-grid-spy-7-10-empirical-downside-roi walk-forward | 2026-09-27T21:36:21+00:00 | index-options-grid-spy-7-10-empirical-downside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-7-10-empirical-downside-roi-walkforward-2026-09-27-904a55cc | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-7-10-empirical-downside-roi-walkforward-2026-09-27-904a55cc | state=ran folds=18 hash=904a55cc asof=2026-09-27 |
| A0499 | execute | index-options-grid-spy-7-10-empirical-upside-roi walk-forward | 2026-09-27T21:36:32+00:00 | index-options-grid-spy-7-10-empirical-upside-roi | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-7-10-empirical-upside-roi-walkforward-2026-09-27-88f0ec06 | /home/russell/dskit/children/index_options/pipeline_runs/index-options-grid-spy-7-10-empirical-upside-roi-walkforward-2026-09-27-88f0ec06 | state=ran folds=18 hash=88f0ec06 asof=2026-09-27 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
