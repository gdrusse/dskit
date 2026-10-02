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
| A0687 | acquire | register-source step1-selection | 2026-10-02T16:31:10+00:00 | --root ./pipeline_runs/step1-selection-source --source  --stream  --mode | e33d97ad982230873a21d682fa77674f510f0da3da6ccc1db69514f1ff5429f1 | ./pipeline_runs/step1-selection-source | connector=localtables |
| A0688 | acquire | backfill step1-selection/selected | 2026-10-02T16:31:10+00:00 | --root ./pipeline_runs/step1-selection-source --source step1-selection --stream selected --mode backfill | f6b8d3a9dee587ee98f278d05d7a11cb547aca61331ef9726e0ab8f558f08379 | ./pipeline_runs/step1-selection-source |  |
| A0689 | execute | step1b-feature-engineering | 2026-10-02T16:31:55+00:00 | step1b-feature-engineering | /home/russell/wt/run-steps/children/index_options/pipeline_runs/feature-engineering/runs/step1b-feature-engineering-2026-10-01-6f4a180f | /home/russell/wt/run-steps/children/index_options/pipeline_runs/feature-engineering/runs/step1b-feature-engineering-2026-10-01-6f4a180f | state=ran hash=6f4a180f asof=2026-10-01 |
| A0690 | acquire | register-source cdf-horizon-panel | 2026-10-02T16:31:55+00:00 | --root ./pipeline_runs/feature-panel-source --source  --stream  --mode | 8dd675f92211921820e770b5618eec500ada200f0714ce5a92cc48d6ae50beb6 | ./pipeline_runs/feature-panel-source | connector=localtables |
| A0691 | acquire | backfill cdf-horizon-panel/input_panel | 2026-10-02T16:31:56+00:00 | --root ./pipeline_runs/feature-panel-source --source cdf-horizon-panel --stream input_panel --mode backfill | 2276c2603baf3b2f645d116db4c126088f42b6e2423fc71049a6663f57199267 | ./pipeline_runs/feature-panel-source |  |
| A0692 | execute | step2-feature-availability | 2026-10-02T16:31:59+00:00 | step2-feature-availability | /home/russell/wt/run-steps/children/index_options/pipeline_runs/step2-feature-availability/runs/step2-feature-availability-2026-10-01-bc5e4ef4 | /home/russell/wt/run-steps/children/index_options/pipeline_runs/step2-feature-availability/runs/step2-feature-availability-2026-10-01-bc5e4ef4 | state=ran hash=bc5e4ef4 asof=2026-10-01 |
| A0693 | acquire | register-source step2-dates | 2026-10-02T16:31:59+00:00 | --root ./pipeline_runs/step2-dates-source --source  --stream  --mode | cf90ba52c33abeef08729d903d4d6e46d17efd48743a6c4a6f24005511a398f7 | ./pipeline_runs/step2-dates-source | connector=localtables |
| A0694 | acquire | backfill step2-dates/dates | 2026-10-02T16:31:59+00:00 | --root ./pipeline_runs/step2-dates-source --source step2-dates --stream dates --mode backfill | 99932243527d7a000a07df363a4497894d1964433e376dc7c00092167a71a6c5 | ./pipeline_runs/step2-dates-source |  |
| A0695 | execute | step3-holdout-folds | 2026-10-02T16:32:14+00:00 | step3-holdout-folds | /home/russell/wt/run-steps/children/index_options/pipeline_runs/step3-holdout-folds/runs/step3-holdout-folds-2026-10-01-2507ac1e | /home/russell/wt/run-steps/children/index_options/pipeline_runs/step3-holdout-folds/runs/step3-holdout-folds-2026-10-01-2507ac1e | state=ran hash=2507ac1e asof=2026-10-01 |
| A0696 | execute | step3-holdout-folds-iwm | 2026-10-02T16:32:30+00:00 | step3-holdout-folds-iwm | /home/russell/wt/run-steps/children/index_options/pipeline_runs/step3-holdout-folds-iwm/runs/step3-holdout-folds-iwm-2026-10-01-c86a0730 | /home/russell/wt/run-steps/children/index_options/pipeline_runs/step3-holdout-folds-iwm/runs/step3-holdout-folds-iwm-2026-10-01-c86a0730 | state=ran hash=c86a0730 asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
