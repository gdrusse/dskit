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
| A0767 | execute | step1-target-dates | 2026-10-02T21:36:29+00:00 | step1-target-dates | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step1-target-dates-2026-10-01-a0a042ad | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step1-target-dates-2026-10-01-a0a042ad | state=ran hash=a0a042ad asof=2026-10-01 |
| A0768 | acquire | register-source handoff | 2026-10-02T21:36:29+00:00 | --root ./pipeline_runs/workflow-regress/step1/onboard_IWM_h7 --source  --stream  --mode | 9fe5e31c4e39c12d9db7ac55d0b8b35a3a2a4e0058f5c8221183e7accfc1896a | ./pipeline_runs/workflow-regress/step1/onboard_IWM_h7 | connector=localtables |
| A0769 | acquire | backfill handoff/target_dates | 2026-10-02T21:36:29+00:00 | --root ./pipeline_runs/workflow-regress/step1/onboard_IWM_h7 --source handoff --stream target_dates --mode backfill | 95373dbe9aabe96a85ba3be4bbe15ee7ce187949f09e0d67f87eaa822d471d00 | ./pipeline_runs/workflow-regress/step1/onboard_IWM_h7 |  |
| A0770 | execute | step1b-feature-engineering | 2026-10-02T21:37:10+00:00 | step1b-feature-engineering | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step1b-feature-engineering-2026-10-01-7fbba096 | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step1b-feature-engineering-2026-10-01-7fbba096 | state=ran hash=7fbba096 asof=2026-10-01 |
| A0771 | acquire | register-source handoff | 2026-10-02T21:37:10+00:00 | --root ./pipeline_runs/workflow-regress/step1b/onboard_IWM_h7 --source  --stream  --mode | 516a7799c8830904330f9919affb89b5f818875b3235ba99c9890d35831b619b | ./pipeline_runs/workflow-regress/step1b/onboard_IWM_h7 | connector=localtables |
| A0772 | acquire | backfill handoff/panel | 2026-10-02T21:37:11+00:00 | --root ./pipeline_runs/workflow-regress/step1b/onboard_IWM_h7 --source handoff --stream panel --mode backfill | 4cacaf593859a38838126aa2bdfbacd94cb75bb3ee9ffd5d26a7e6213eee5e79 | ./pipeline_runs/workflow-regress/step1b/onboard_IWM_h7 |  |
| A0773 | execute | step2-feature-availability | 2026-10-02T21:37:09+00:00 | step2-feature-availability | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step2-feature-availability-2026-10-01-8a818997 | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step2-feature-availability-2026-10-01-8a818997 | state=ran hash=8a818997 asof=2026-10-01 |
| A0774 | acquire | register-source handoff | 2026-10-02T21:37:10+00:00 | --root ./pipeline_runs/workflow-regress/step2/onboard_IWM_h7 --source  --stream  --mode | 11bddcae6baae3a615b4a0dbe2076b04baf1056008d686aaf9b9aa00132df9b9 | ./pipeline_runs/workflow-regress/step2/onboard_IWM_h7 | connector=localtables |
| A0775 | acquire | backfill handoff/dates | 2026-10-02T21:37:10+00:00 | --root ./pipeline_runs/workflow-regress/step2/onboard_IWM_h7 --source handoff --stream dates --mode backfill | 0cfbac76ed788a226fd7dc391c733ca64c7cc399b09ba78334cbee1cbc5c5f0e | ./pipeline_runs/workflow-regress/step2/onboard_IWM_h7 |  |
| A0776 | execute | step3-holdout-folds | 2026-10-02T21:37:10+00:00 | step3-holdout-folds | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step3/runs_IWM_h7/step3-holdout-folds-2026-10-01-7c2f5105 | /home/russell/wt/claude-sonnet-5-5/children/index_options/pipeline_runs/workflow-regress/step3/runs_IWM_h7/step3-holdout-folds-2026-10-01-7c2f5105 | state=ran hash=7c2f5105 asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
