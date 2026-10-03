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
| A1005 | execute | step1-target-dates | 2026-10-03T18:23:59+00:00 | step1-target-dates | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step1-target-dates-2026-10-01-6066fa1f | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step1-target-dates-2026-10-01-6066fa1f | state=ran hash=6066fa1f asof=2026-10-01 |
| A1006 | acquire | register-source handoff | 2026-10-03T18:23:59+00:00 | --root pipeline_runs/top5-opt/MSTR/step1/onboard_MSTR_h7 --source  --stream  --mode | 3a61ae1b3d69a28c62e80043ec07beecb5aa1f24710cfd5651f944e1f3150003 | pipeline_runs/top5-opt/MSTR/step1/onboard_MSTR_h7 | connector=localtables |
| A1007 | acquire | backfill handoff/target_dates | 2026-10-03T18:23:59+00:00 | --root pipeline_runs/top5-opt/MSTR/step1/onboard_MSTR_h7 --source handoff --stream target_dates --mode backfill | 83fc333202ec71ebe6af66a2b5fc3c1b06bc88594d3d545de9ef41db7447a1c4 | pipeline_runs/top5-opt/MSTR/step1/onboard_MSTR_h7 |  |
| A1008 | execute | step1b-feature-engineering | 2026-10-03T18:24:48+00:00 | step1b-feature-engineering | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step1b-feature-engineering-2026-10-01-bff956c8 | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step1b-feature-engineering-2026-10-01-bff956c8 | state=ran hash=bff956c8 asof=2026-10-01 |
| A1009 | acquire | register-source handoff | 2026-10-03T18:24:48+00:00 | --root pipeline_runs/top5-opt/MSTR/step1b/onboard_MSTR_h7 --source  --stream  --mode | fef578b06f87d5ade095a8af595dd8c71d7b0254301cf37e24d247553980c60f | pipeline_runs/top5-opt/MSTR/step1b/onboard_MSTR_h7 | connector=localtables |
| A1010 | acquire | backfill handoff/panel | 2026-10-03T18:24:48+00:00 | --root pipeline_runs/top5-opt/MSTR/step1b/onboard_MSTR_h7 --source handoff --stream panel --mode backfill | 82b52b9a0e86b7e5381dc7cbc97682491b826a54fb47ced706c68f505d9530c1 | pipeline_runs/top5-opt/MSTR/step1b/onboard_MSTR_h7 |  |
| A1011 | execute | step2-feature-availability | 2026-10-03T18:24:49+00:00 | step2-feature-availability | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step2-feature-availability-2026-10-01-fa3df0dd | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step2-feature-availability-2026-10-01-fa3df0dd | state=ran hash=fa3df0dd asof=2026-10-01 |
| A1012 | acquire | register-source handoff | 2026-10-03T18:24:49+00:00 | --root pipeline_runs/top5-opt/MSTR/step2/onboard_MSTR_h7 --source  --stream  --mode | 135d2ffe8714b9ccd44c3286748437fda6f6c1138477227e364f668bb9b2dcd6 | pipeline_runs/top5-opt/MSTR/step2/onboard_MSTR_h7 | connector=localtables |
| A1013 | acquire | backfill handoff/dates | 2026-10-03T18:24:49+00:00 | --root pipeline_runs/top5-opt/MSTR/step2/onboard_MSTR_h7 --source handoff --stream dates --mode backfill | 154bb305808b1f9207e9c1dd61d364c96b105c04736eac07a2de70e7557d5284 | pipeline_runs/top5-opt/MSTR/step2/onboard_MSTR_h7 |  |
| A1014 | execute | step3-holdout-folds | 2026-10-03T18:24:49+00:00 | step3-holdout-folds | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step3/runs_MSTR_h7/step3-holdout-folds-2026-10-01-26458bf4 | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step3/runs_MSTR_h7/step3-holdout-folds-2026-10-01-26458bf4 | state=ran hash=26458bf4 asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
