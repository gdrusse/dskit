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
| A1037 | acquire | register-source handoff | 2026-10-03T22:44:14+00:00 | --root ./pipeline_runs/pooled-heads-top5/plan/step1b/onboard_NVDA_h31 --source  --stream  --mode | 8581222ad6e5eff462ed93ec71c65b5a65a6044fa9db5c18c0ed5cd3ac950465 | ./pipeline_runs/pooled-heads-top5/plan/step1b/onboard_NVDA_h31 | connector=localtables |
| A1038 | acquire | backfill handoff/panel | 2026-10-03T22:44:15+00:00 | --root ./pipeline_runs/pooled-heads-top5/plan/step1b/onboard_NVDA_h31 --source handoff --stream panel --mode backfill | 87f99f35c33892782d4d65306dcbd96155e8b4a7c93a516ef0208868e368e051 | ./pipeline_runs/pooled-heads-top5/plan/step1b/onboard_NVDA_h31 |  |
| A1039 | execute | step2-feature-availability | 2026-10-03T22:44:15+00:00 | step2-feature-availability | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step2-feature-availability-2026-10-01-469f6e8b | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step2-feature-availability-2026-10-01-469f6e8b | state=ran hash=469f6e8b asof=2026-10-01 |
| A1040 | acquire | register-source handoff | 2026-10-03T22:44:16+00:00 | --root ./pipeline_runs/pooled-heads-top5/plan/step2/onboard_NVDA_h31 --source  --stream  --mode | fc49930b764c85587b0317ed3196ede652d94b8ec6a89292152c41c16878a3ce | ./pipeline_runs/pooled-heads-top5/plan/step2/onboard_NVDA_h31 | connector=localtables |
| A1041 | acquire | backfill handoff/dates | 2026-10-03T22:44:16+00:00 | --root ./pipeline_runs/pooled-heads-top5/plan/step2/onboard_NVDA_h31 --source handoff --stream dates --mode backfill | 605d50fb3872432a1a0347569a516476f9abfb8bddbdd4e7dc888c22b48db642 | ./pipeline_runs/pooled-heads-top5/plan/step2/onboard_NVDA_h31 |  |
| A1042 | execute | step3-holdout-folds | 2026-10-03T22:44:16+00:00 | step3-holdout-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-2084a113 | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-2084a113 | state=ran hash=2084a113 asof=2026-10-01 |
| A1043 | execute | step3-holdout-folds | 2026-10-03T22:44:51+00:00 | step3-holdout-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-6f3ea30f | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-6f3ea30f | state=error hash=6f3ea30f asof=2026-10-01 |
| A1044 | execute | step3-holdout-folds | 2026-10-03T22:45:17+00:00 | step3-holdout-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-6f3ea30f | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-6f3ea30f | state=ran hash=6f3ea30f asof=2026-10-01 |
| A1045 | execute | pooled-heads-top5-folds | 2026-10-03T22:46:02+00:00 | pooled-heads-top5-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-f04d426a | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-f04d426a | state=error hash=f04d426a asof=2026-10-01 |
| A1046 | execute | pooled-heads-top5-folds | 2026-10-03T22:46:15+00:00 | pooled-heads-top5-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-623a093f | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-623a093f | state=ran hash=623a093f asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
