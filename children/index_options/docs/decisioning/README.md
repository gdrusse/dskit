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
| A0603 | research | compare conditional CDF approaches for exact-expiry condor payoffs | 2026-09-28T18:52:18+00:00 | primary literature; nine-ref repository sweep; owner price-query CDF objective | docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md | docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md | expands=A0602 empirical_monotone_cdf_quantile_lightgbm_mixture_mlp numerical_payoff_identity=passed training=not_run |
| A0604 | execute | bounded exact-expiry predictive CDF comparison and recommendation | 2026-09-28T19:55:35+00:00 | configs/run-predictive-cdf-comparison.json source=0fa4da8b config_sha256=ad84da428d4763b123589b4e94833c654c58242db2d0842e77f722e1d5049978 | docs/memos/2026-09-28-predictive-cdf-comparison.md; ../../docs/review-evidence/ADR-0189.md | pipeline_runs/predictive_cdf_reviewed_20260928 | state=ran models=6 folds=30 features=39 dev_rows=19014 eval_rows=68084 exact_day_cells=135 wall_seconds=629.56 peak_host_kib=3047744 empirical_reference_retained mlp3_spy_challenger no_trading fresh_reviews_C0_M0 tests=43 superseded_initial_run=predictive_cdf_20260928 |
| A0605 | research | distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo | 2026-09-28T20:18:29+00:00 | distribution assumptions and pooled-head CDF HPO | docs/research/distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo.md | docs/research/distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo.md |  |
| A0606 | execute | cdf-hpo-pooled-heads | 2026-09-28T21:18:57+00:00 | configs/run-predictive-cdf-hpo.json | docs/memos/2026-09-28-predictive-cdf-hpo.md | pipeline_runs/predictive_cdf_hpo_20260928 | All24 neural candidates and9final models completed viaJSON CLI; Luna reviews closed; fullCDF no lift; pooled boundedlossMSE +3.05percent secondary; no trading |
| A0607 | execute | cdf-additional-methods-comparison | 2026-09-28T23:09:39+00:00 | configs/run-predictive-cdf-methods.json | docs/memos/2026-09-28-predictive-cdf-methods.md | pipeline_runs/predictive_cdf_methods_20260928 | nine_candidates_complete_135_cells_blend_skill_0.818_percent_C0_M0_no_trading |
| A0608 | execute | cdf_blend_refinement | 2026-09-29T00:21:59+00:00 | configs/run-predictive-cdf-refinement.json | docs/memos/2026-09-28-predictive-cdf-refinement.md | pipeline_runs/predictive_cdf_refinement_20260928 | 13_candidates_complete_primary_small035_gain0150pct_uncertain_downside_worse_no_trading |
| A0609 | execute | cdf_downside_iteration | 2026-09-29T01:41:03+00:00 | configs/run-predictive-cdf-downside.json | docs/memos/2026-09-28-predictive-cdf-downside.md | pipeline_runs/predictive_cdf_downside_20260928 | nine_candidates_complete_floor_gain0164pct_uncertain_guard_failed_keep_incumbent_C0_M0_no_trading |
| A0610 | execute | cdf_option_surface_tail_adaptive | 2026-09-29T18:56:18+00:00 | configs/run-predictive-cdf-option-surface.json | docs/memos/2026-09-29-predictive-cdf-option-surface.md | pipeline_runs/predictive_cdf_option_surface_20260929 | 13_candidates_complete_135_cells_none_passed_guard_keep_incumbent_no_trading |
| A0611 | research | strategy-alternatives/2026-09-30-survey | 2026-09-30T13:22:30+00:00 | Index-option strategies beyond the iron condor: evidence survey | docs/research/strategy-alternatives/2026-09-30-survey.md | docs/research/strategy-alternatives/2026-09-30-survey.md |  |
| A0612 | research | strategy-alternatives/2026-09-30-synthesis | 2026-09-30T13:22:30+00:00 | Strategy alternatives memo: what to model after the condor | docs/research/strategy-alternatives/2026-09-30-synthesis.md | docs/research/strategy-alternatives/2026-09-30-synthesis.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
