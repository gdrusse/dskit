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
| A0885 | execute | step2-feature-availability | 2026-10-03T16:23:24+00:00 | step2-feature-availability | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/stocks-long/step2-feature-availability-2026-10-01-cc56ac1b | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/stocks-long/step2-feature-availability-2026-10-01-cc56ac1b | state=ran hash=cc56ac1b asof=2026-10-01 |
| A0886 | acquire | register-source handoff | 2026-10-03T16:23:24+00:00 | --root ./pipeline_runs/stocks-long/step2/onboard_AAPL_h7 --source  --stream  --mode | ed20aabf92758b82c073a62c394077394bdbaf517ea08476d463de2eceaa736b | ./pipeline_runs/stocks-long/step2/onboard_AAPL_h7 | connector=localtables |
| A0887 | acquire | backfill handoff/dates | 2026-10-03T16:23:24+00:00 | --root ./pipeline_runs/stocks-long/step2/onboard_AAPL_h7 --source handoff --stream dates --mode backfill | 294d491902ee2ab19d6c57f8536ba5727f1b3b5046dbb5ce26b1a9ab2e22ffaa | ./pipeline_runs/stocks-long/step2/onboard_AAPL_h7 |  |
| A0888 | execute | step3-holdout-folds | 2026-10-03T16:23:25+00:00 | step3-holdout-folds | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/stocks-long/step3/runs_AAPL_h7/step3-holdout-folds-2026-10-01-30370259 | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/stocks-long/step3/runs_AAPL_h7/step3-holdout-folds-2026-10-01-30370259 | state=ran hash=30370259 asof=2026-10-01 |
| A0889 | execute | features-stock-option-trades | 2026-10-03T16:25:11+00:00 | features-stock-option-trades | /tmp/claude-1000/-home-russell-dskit/68909909-20b5-4f41-8df8-dd0d228dee2a/scratchpad/r2/features-stock-option-trades-2026-10-03-17d0f42b | /tmp/claude-1000/-home-russell-dskit/68909909-20b5-4f41-8df8-dd0d228dee2a/scratchpad/r2/features-stock-option-trades-2026-10-03-17d0f42b | state=ran hash=17d0f42b asof=2026-10-03 |
| A0890 | acquire | register-source stock-option-trade-features-r2 | 2026-10-03T16:25:11+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 62f73c68032e1a996929bf1399e8526d9297287cbcdd9787ed7f3276f1fdbf93 | /home/russell/data/index_options/ob | connector=localtables |
| A0891 | acquire | backfill stock-option-trade-features-r2/stock_option_trade_features | 2026-10-03T16:25:13+00:00 | --root /home/russell/data/index_options/ob --source stock-option-trade-features-r2 --stream stock_option_trade_features --mode backfill | a5edf28b7fa6d25273e72f7612d494a296a6b41b4b250d91d387fec4afbebe28 | /home/russell/data/index_options/ob |  |
| A0892 | execute | step1-target-dates | 2026-10-03T16:48:27+00:00 | step1-target-dates | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/workflow/step1-target-dates-2026-10-01-fbc13abd | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/workflow/step1-target-dates-2026-10-01-fbc13abd | state=ran hash=fbc13abd asof=2026-10-01 |
| A0893 | acquire | register-source handoff | 2026-10-03T16:48:27+00:00 | --root ./pipeline_runs/workflow/step1/onboard_AAPL_h7 --source  --stream  --mode | 114ab071d25bdc203b0302f3486b97eec5e6e1769370f4f836ddb82f6726c833 | ./pipeline_runs/workflow/step1/onboard_AAPL_h7 | connector=localtables |
| A0894 | acquire | backfill handoff/target_dates | 2026-10-03T16:48:28+00:00 | --root ./pipeline_runs/workflow/step1/onboard_AAPL_h7 --source handoff --stream target_dates --mode backfill | 27c073f90ec5601d0b1a0981a632bc185901980088b574aa4982dd2e4b6326af | ./pipeline_runs/workflow/step1/onboard_AAPL_h7 |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
