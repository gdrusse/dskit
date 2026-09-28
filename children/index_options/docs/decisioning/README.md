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
| A0585 | execute | index-options-spy-21-gru-temporal-vol walk-forward | 2026-09-28T12:38:25+00:00 | index-options-spy-21-gru-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-21-gru-temporal-vol-walkforward-2026-09-27-38a17899 | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-21-gru-temporal-vol-walkforward-2026-09-27-38a17899 | state=ran folds=18 hash=38a17899 asof=2026-09-27 |
| A0586 | execute | index-options-spy-30-45-mlp-temporal-vol walk-forward | 2026-09-28T12:38:43+00:00 | index-options-spy-30-45-mlp-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-30-45-mlp-temporal-vol-walkforward-2026-09-27-ca011a85 | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-30-45-mlp-temporal-vol-walkforward-2026-09-27-ca011a85 | state=ran folds=18 hash=ca011a85 asof=2026-09-27 |
| A0587 | execute | index-options-spy-5-mlp-temporal-vol walk-forward | 2026-09-28T12:38:43+00:00 | index-options-spy-5-mlp-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-5-mlp-temporal-vol-walkforward-2026-09-27-718a26ce | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-5-mlp-temporal-vol-walkforward-2026-09-27-718a26ce | state=ran folds=18 hash=718a26ce asof=2026-09-27 |
| A0588 | execute | index-options-spy-30-45-gru-temporal-vol walk-forward | 2026-09-28T12:39:20+00:00 | index-options-spy-30-45-gru-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-30-45-gru-temporal-vol-walkforward-2026-09-27-e2e27d9e | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-30-45-gru-temporal-vol-walkforward-2026-09-27-e2e27d9e | state=ran folds=18 hash=e2e27d9e asof=2026-09-27 |
| A0589 | execute | index-options-spy-5-gru-temporal-vol walk-forward | 2026-09-28T12:39:21+00:00 | index-options-spy-5-gru-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-5-gru-temporal-vol-walkforward-2026-09-27-754bc61b | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-5-gru-temporal-vol-walkforward-2026-09-27-754bc61b | state=ran folds=18 hash=754bc61b asof=2026-09-27 |
| A0590 | execute | index-options-spy-7-10-mlp-temporal-vol walk-forward | 2026-09-28T12:39:37+00:00 | index-options-spy-7-10-mlp-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-7-10-mlp-temporal-vol-walkforward-2026-09-27-4b77e902 | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-7-10-mlp-temporal-vol-walkforward-2026-09-27-4b77e902 | state=ran folds=18 hash=4b77e902 asof=2026-09-27 |
| A0591 | execute | index-options-spy-7-10-gru-temporal-vol walk-forward | 2026-09-28T12:40:12+00:00 | index-options-spy-7-10-gru-temporal-vol | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-7-10-gru-temporal-vol-walkforward-2026-09-27-3051985c | /home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/temporal_feature_selection/runs/index-options-spy-7-10-gru-temporal-vol-walkforward-2026-09-27-3051985c | state=ran folds=18 hash=3051985c asof=2026-09-27 |
| A0592 | research | distribution-modeling/2026-09-28-temporal-mlp-gru-screen | 2026-09-28T12:46:40+00:00 | Temporal MLP/GRU screen across 21 index-option cells | docs/research/distribution-modeling/2026-09-28-temporal-mlp-gru-screen.md | docs/research/distribution-modeling/2026-09-28-temporal-mlp-gru-screen.md |  |
| A0593 | research | distribution-modeling/2026-09-28-fixed-feature-model-zoo-selection | 2026-09-28T13:40:14+00:00 | Fixed-feature model zoo selects Ridge for forward volatility | docs/research/distribution-modeling/2026-09-28-fixed-feature-model-zoo-selection.md | docs/research/distribution-modeling/2026-09-28-fixed-feature-model-zoo-selection.md |  |
| A0594 | execute | fixed-feature 18-model variability zoo | 2026-09-28T13:40:48+00:00 | protocol_sha256=4ae68c81539b99134ac9351ea80d098e07f20d7bc357f4581ae3d41a96ae4afa | summary_sha256=0cbb4a34cd2977d161c6d952aabd40fe6af558f3a2154a2bbb233d0ab6c13873 | pipeline_runs/feature_zoo_20260928 | state=ran feature_fits=648 zoo_fits=2646 models=18 cells=21 folds_per_model=147 champion=ridge point_winner=elasticnet neural=mlp challenger=tcn device=cpu cuda_verified_afterward |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
