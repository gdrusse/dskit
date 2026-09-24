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
| A0024 | execute | staged run | 2026-09-24T01:58:28+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json |  |  | stage_token=4f7f4f55d0cb1b43e1a1b65509891350ee9eb0494dd0ef10db8521f6947139de:run; state=error; reason=ValueError: walk-forward summary dir /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-f7865f91 already exists and is not empty — same name+asof+identity means this exact evaluation already happened; remove it deliberately to repeat |
| A0025 | acquire | register-source cboe-chain-wide | 2026-09-24T02:00:58+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 922eceb3b7266b3fb571b77cba51dd329da707cd78abf6052acc67d47411aaee | /home/russell/data/index_options/ob | connector=cboe |
| A0026 | acquire | register-source cboe-index-wide | 2026-09-24T02:00:58+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | a226b63bbdf7b91029382175732e44d761f22c1ba85f10a2534f96aae797f7ab | /home/russell/data/index_options/ob | connector=cboe |
| A0027 | acquire | backfill cboe-index-wide/index_daily | 2026-09-24T02:01:29+00:00 | --root /home/russell/data/index_options/ob --source cboe-index-wide --stream index_daily --mode backfill | 07e19c3e751b18c5a158e2e059bbd66e77b61c95380e7487ec4b4686017f84f9 | /home/russell/data/index_options/ob |  |
| A0028 | acquire | register-source cboe-chain-wide | 2026-09-24T02:02:56+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 6481e2d70b733101993ebbb3cd3a3ea79e8669e2d3ae7d7728030f0c3d4045e3 | /home/russell/data/index_options/ob | connector=cboe |
| A0029 | acquire | register-source cboe-chain-wide | 2026-09-24T02:02:59+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 6481e2d70b733101993ebbb3cd3a3ea79e8669e2d3ae7d7728030f0c3d4045e3 | /home/russell/data/index_options/ob | connector=cboe |
| A0030 | acquire | live cboe-chain-wide/option_chain | 2026-09-24T02:04:09+00:00 | --root /home/russell/data/index_options/ob --source cboe-chain-wide --stream option_chain --mode live | 57ddc430a481a68715608f0c531da80d5df27179f5b2ccd0fa8b90cc9c4025e3 | /home/russell/data/index_options/ob |  |
| A0031 | acquire | live cboe-chain-wide/option_chain | 2026-09-24T02:05:07+00:00 | --root /home/russell/data/index_options/ob --source cboe-chain-wide --stream option_chain --mode live | 4152267f72cd8fb005942363f639a35dcd6f39458fc5a8eace002c21d35a1959 | /home/russell/data/index_options/ob |  |
| A0032 | acquire | backfill cboe-index-wide/index_daily | 2026-09-24T02:05:36+00:00 | --root /home/russell/data/index_options/ob --source cboe-index-wide --stream index_daily --mode backfill |  | /home/russell/data/index_options/ob |  |
| A0033 | acquire | backfill cboe-index/index_daily | 2026-09-24T02:05:39+00:00 | --root /home/russell/data/index_options/ob --source cboe-index --stream index_daily --mode backfill |  | /home/russell/data/index_options/ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
