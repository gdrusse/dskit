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
| A1056 | acquire | backfill handoff/panel | 2026-10-04T21:45:36+00:00 | --root ./pipeline_runs/pooled-zoo-417/plan/step1b/onboard_NVDA_h31 --source handoff --stream panel --mode backfill | ab68ed0fa42b5d5e71350bc201e2013f559abdb3d005a3e34af6691f2f2e4a81 | ./pipeline_runs/pooled-zoo-417/plan/step1b/onboard_NVDA_h31 |  |
| A1057 | execute | step2-feature-availability | 2026-10-04T21:45:42+00:00 | step2-feature-availability | /home/russell/wt/model-zoo-417/children/index_options/pipeline_runs/pooled-zoo-417/plan/step2-feature-availability-2026-10-01-1509e909 | /home/russell/wt/model-zoo-417/children/index_options/pipeline_runs/pooled-zoo-417/plan/step2-feature-availability-2026-10-01-1509e909 | state=ran hash=1509e909 asof=2026-10-01 |
| A1058 | acquire | register-source handoff | 2026-10-04T21:45:42+00:00 | --root ./pipeline_runs/pooled-zoo-417/plan/step2/onboard_NVDA_h31 --source  --stream  --mode | cd105ac0452f7b9dc67170f4762d41149db357e8fd2b1f6ed3843410fe89d569 | ./pipeline_runs/pooled-zoo-417/plan/step2/onboard_NVDA_h31 | connector=localtables |
| A1059 | acquire | backfill handoff/dates | 2026-10-04T21:45:42+00:00 | --root ./pipeline_runs/pooled-zoo-417/plan/step2/onboard_NVDA_h31 --source handoff --stream dates --mode backfill | 35ea110da319bdef54db1a9659fcc2f5c1f029e3c13bb7c3d8662f5ec7e02bfb | ./pipeline_runs/pooled-zoo-417/plan/step2/onboard_NVDA_h31 |  |
| A1060 | execute | step3-holdout-folds | 2026-10-04T21:45:42+00:00 | step3-holdout-folds | /home/russell/wt/model-zoo-417/children/index_options/pipeline_runs/pooled-zoo-417/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-ce9ded48 | /home/russell/wt/model-zoo-417/children/index_options/pipeline_runs/pooled-zoo-417/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-ce9ded48 | state=ran hash=ce9ded48 asof=2026-10-01 |
| A1061 | execute | pooled-zoo-417-folds | 2026-10-04T21:47:59+00:00 | pooled-zoo-417-folds | /home/russell/wt/model-zoo-417/children/index_options/pipeline_runs/pooled-zoo-417/folds/pooled-zoo-417-folds-2026-10-01-261df98d | /home/russell/wt/model-zoo-417/children/index_options/pipeline_runs/pooled-zoo-417/folds/pooled-zoo-417-folds-2026-10-01-261df98d | state=ran hash=261df98d asof=2026-10-01 |
| A1062 | execute | pooled-zoo-417-panel-smoke | 2026-10-04T21:59:47+00:00 | /home/russell/dskit/.venv/bin/python /home/russell/zoo_panel_smoke.py | pipeline_runs/pooled-zoo-417/smoke |  | ADR-0236/0237 smoke: build the 416-ticker pooled panel once (rows, refused, time, RSS, expected cells); no training; exit 0 |
| A1063 | execute | pooled-zoo-417-search-smoke | 2026-10-04T22:02:49+00:00 | /home/russell/dskit/.venv/bin/python -W ignore /home/russell/zoo_search_smoke.py | pipeline_runs/pooled-zoo-417/smoke/hpo |  | ADR-0236 smoke: one candidate per kind (mlp, gru, lstm, cnn, lgbm; base features) through the real search stage on the warm-up fold, reusing the smoke panel; timing only; exit 1 |
| A1064 | execute | pooled-zoo-417-search-smoke2-search | 2026-10-04T23:59:40+00:00 | /home/russell/dskit/.venv/bin/python -W ignore -m index_options.cdf_study pipeline_runs/pooled-zoo-417/smoke/smoke-config.json --stage search --partition smoke | pipeline_runs/pooled-zoo-417/smoke/hpo2 |  | ADR-0236 amendment 2 smoke: one base candidate per kind (mlp, gru, lstm, cnn, lgbm) through the real CLI search stage on the warm-up fold, min_task_fit_rows 40; search builds the panel cache, select must reuse it; timing only; exit 0 |
| A1065 | execute | pooled-zoo-417-search-smoke2-select | 2026-10-04T23:59:56+00:00 | /home/russell/dskit/.venv/bin/python -W ignore -m index_options.cdf_study pipeline_runs/pooled-zoo-417/smoke/smoke-config.json --stage select | pipeline_runs/pooled-zoo-417/smoke/hpo2 |  | ADR-0236 amendment 2 smoke: one base candidate per kind (mlp, gru, lstm, cnn, lgbm) through the real CLI select stage on the warm-up fold, min_task_fit_rows 40; search builds the panel cache, select must reuse it; timing only; exit 0 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
