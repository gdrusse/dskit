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
| A1044 | execute | step3-holdout-folds | 2026-10-03T22:45:17+00:00 | step3-holdout-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-6f3ea30f | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/plan/step3/runs_NVDA_h31/step3-holdout-folds-2026-10-01-6f3ea30f | state=ran hash=6f3ea30f asof=2026-10-01 |
| A1045 | execute | pooled-heads-top5-folds | 2026-10-03T22:46:02+00:00 | pooled-heads-top5-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-f04d426a | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-f04d426a | state=error hash=f04d426a asof=2026-10-01 |
| A1046 | execute | pooled-heads-top5-folds | 2026-10-03T22:46:15+00:00 | pooled-heads-top5-folds | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-623a093f | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/pooled-heads-top5/folds/pooled-heads-top5-folds-2026-10-01-623a093f | state=ran hash=623a093f asof=2026-10-01 |
| A1047 | acquire | backfill options/bars | 2026-10-03T23:59:08+00:00 | --root /home/russell/data/stock_options/option-universe-300 --source options --stream bars --mode backfill | 6af56b36322907a09b5cf3c271ec18e2d3a980ae78340c2d3557c57e1591c033 | /home/russell/data/stock_options/option-universe-300 |  |
| A1048 | execute | features-stock-option-trades | 2026-10-04T00:03:47+00:00 | features-stock-option-trades | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/stock-features-300/features-stock-option-trades-2026-10-03-a64e9384 | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/stock-features-300/features-stock-option-trades-2026-10-03-a64e9384 | state=ran hash=a64e9384 asof=2026-10-03 |
| A1049 | acquire | register-source stock-option-trade-features-300 | 2026-10-04T00:03:47+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | d192d4eb8acf9cc1cf67fc0b3b4f017e802d46a63485304dda97ae5a651c6a91 | /home/russell/data/index_options/ob | connector=localtables |
| A1050 | acquire | backfill stock-option-trade-features-300/stock_option_trade_features | 2026-10-04T00:03:52+00:00 | --root /home/russell/data/index_options/ob --source stock-option-trade-features-300 --stream stock_option_trade_features --mode backfill | 2fd861ece65c87010b38cc4eba9430572124e4f359d173021dc8f4c2dfa6dcfe | /home/russell/data/index_options/ob |  |
| A1051 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T16:29:23+00:00 | Pilot ideas to lift predictive-CDF skill over the empirical baseline | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |
| A1052 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T16:42:47+00:00 | Pilot ideas to lift predictive-CDF skill over the empirical baseline (review corrections) | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |
| A1053 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T16:55:35+00:00 | Pilot ideas to lift predictive-CDF skill (second review: window reading rule, training budget) | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
