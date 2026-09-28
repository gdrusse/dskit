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
| A0598 | research | distribution-modeling/2026-09-28-exact-expiry-tcn-output-and-day-skill | 2026-09-28T15:37:25+00:00 | Exact-expiry TCN output, validation, and day-level skill | docs/research/distribution-modeling/2026-09-28-exact-expiry-tcn-output-and-day-skill.md | docs/research/distribution-modeling/2026-09-28-exact-expiry-tcn-output-and-day-skill.md |  |
| A0599 | execute | exact-expiry feature selection, 18-model zoo, TCN HPO, and locked evaluation | 2026-09-28T15:38:18+00:00 | protocol_sha256=41de7168cf08945d17e0248288215acde46c138ec3a947aae01388e8477a5733 archive_rows=53407120 expiry_series=5182 | zoo_sha256=1e63946edc29946846486eb4f6f6a6b6b960418d3cbada32bedfbb5599cedbfb predictions_sha256=9aace8020713dd75d52fbfcf2be98de7502a31e99a2b5484d23f8f55fce3b7b7 summary_sha256=7543c69451f551d5e24f7f8cb673d53e8d32a7f136c8cd96439002ad50d8bb29 | pipeline_runs/exact_maturity_20260928 | state=ran target=exact_expiry_integrated_variance features=49 models=18 hpo_candidates=36 hpo_fits=648 evaluation_rows=68084 champion=tcn full_equal_day_skill=0.1599 recent_equal_day_skill=0.0043 cuda=rtx5060ti censored_rows=refused |
| A0600 | research | corrected exact-expiry model and output memos with sample sizes | 2026-09-28T17:29:54+00:00 | raw-variance diagnosis; corrected daily-RMS evidence | docs/research/distribution-modeling/2026-09-28-exact-expiry-feature-selection-model-zoo.md; docs/research/distribution-modeling/2026-09-28-exact-expiry-tcn-output-and-day-skill.md |  | supersedes=A0597,A0598 counts=all_exact_dte_cells train_validation=all_folds |
| A0601 | execute | rerun exact-expiry selection, zoo, ElasticNet HPO, and evaluation | 2026-09-28T17:29:55+00:00 | protocol_sha256=dda6da89b2ca74d9112e8196555d8f0385c26d99a1bcb207e0e2a66da6904253 archive_rows=53407120 expiry_series=5182 | zoo_sha256=7f8b6a7f068314e9b67ba42addd60bded7c9aac0a575bb931161f6f198dbd4b5 predictions_sha256=15a506251a0a857a670cc81067090e1ade42a7b4293e77b3922efd101fce89b1 summary_sha256=86a02044efc793af1b75c9e083eb19de894f1f30acda6e8db856d989f26ddab5 | pipeline_runs/exact_maturity_rms_20260928 | supersedes=A0599 state=ran target=exact_window_daily_rms features=38 models=18 hpo_candidates=90 hpo_fits=1620 evaluation_rows=68084 champion=elasticnet alpha=0.03 l1_ratio=0.75 full_equal_day_skill=0.4037 recent_equal_day_skill=0.3893 cell_counts=reported fold_counts=reported |
| A0602 | research | distribution-modeling/2026-09-28-predictive-cdf-condor-research | 2026-09-28T18:42:17+00:00 | Predictive CDF research for exact-expiry condor expected returns | docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md | docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md |  |
| A0603 | research | compare conditional CDF approaches for exact-expiry condor payoffs | 2026-09-28T18:52:18+00:00 | primary literature; nine-ref repository sweep; owner price-query CDF objective | docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md | docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md | expands=A0602 empirical_monotone_cdf_quantile_lightgbm_mixture_mlp numerical_payoff_identity=passed training=not_run |
| A0604 | execute | bounded exact-expiry predictive CDF comparison and recommendation | 2026-09-28T19:55:35+00:00 | configs/run-predictive-cdf-comparison.json source=0fa4da8b config_sha256=ad84da428d4763b123589b4e94833c654c58242db2d0842e77f722e1d5049978 | docs/memos/2026-09-28-predictive-cdf-comparison.md; ../../docs/review-evidence/ADR-0189.md | pipeline_runs/predictive_cdf_reviewed_20260928 | state=ran models=6 folds=30 features=39 dev_rows=19014 eval_rows=68084 exact_day_cells=135 wall_seconds=629.56 peak_host_kib=3047744 empirical_reference_retained mlp3_spy_challenger no_trading fresh_reviews_C0_M0 tests=43 superseded_initial_run=predictive_cdf_20260928 |
| A0605 | research | distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo | 2026-09-28T20:18:29+00:00 | distribution assumptions and pooled-head CDF HPO | docs/research/distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo.md | docs/research/distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo.md |  |
| A0606 | execute | cdf-hpo-pooled-heads | 2026-09-28T21:18:57+00:00 | configs/run-predictive-cdf-hpo.json | docs/memos/2026-09-28-predictive-cdf-hpo.md | pipeline_runs/predictive_cdf_hpo_20260928 | All24 neural candidates and9final models completed viaJSON CLI; Luna reviews closed; fullCDF no lift; pooled boundedlossMSE +3.05percent secondary; no trading |
| A0607 | execute | cdf-additional-methods-comparison | 2026-09-28T23:09:39+00:00 | configs/run-predictive-cdf-methods.json | docs/memos/2026-09-28-predictive-cdf-methods.md | pipeline_runs/predictive_cdf_methods_20260928 | nine_candidates_complete_135_cells_blend_skill_0.818_percent_C0_M0_no_trading |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
