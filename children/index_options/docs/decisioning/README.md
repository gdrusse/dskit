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
| A0607 | execute | cdf-additional-methods-comparison | 2026-09-28T23:09:39+00:00 | configs/run-predictive-cdf-methods.json | docs/memos/2026-09-28-predictive-cdf-methods.md | pipeline_runs/predictive_cdf_methods_20260928 | nine_candidates_complete_135_cells_blend_skill_0.818_percent_C0_M0_no_trading |
| A0608 | execute | cdf_blend_refinement | 2026-09-29T00:21:59+00:00 | configs/run-predictive-cdf-refinement.json | docs/memos/2026-09-28-predictive-cdf-refinement.md | pipeline_runs/predictive_cdf_refinement_20260928 | 13_candidates_complete_primary_small035_gain0150pct_uncertain_downside_worse_no_trading |
| A0609 | execute | cdf_downside_iteration | 2026-09-29T01:41:03+00:00 | configs/run-predictive-cdf-downside.json | docs/memos/2026-09-28-predictive-cdf-downside.md | pipeline_runs/predictive_cdf_downside_20260928 | nine_candidates_complete_floor_gain0164pct_uncertain_guard_failed_keep_incumbent_C0_M0_no_trading |
| A0610 | execute | cdf_option_surface_tail_adaptive | 2026-09-29T18:56:18+00:00 | configs/run-predictive-cdf-option-surface.json | docs/memos/2026-09-29-predictive-cdf-option-surface.md | pipeline_runs/predictive_cdf_option_surface_20260929 | 13_candidates_complete_135_cells_none_passed_guard_keep_incumbent_no_trading |
| A0611 | execute | cdf_risk_neutral_architecture_comparison | 2026-09-30T00:05:52+00:00 | configs/run-predictive-cdf-risk-neutral.json | docs/memos/2026-09-29-risk-neutral-cdf-architecture-comparison.md | pipeline_runs/predictive_cdf_risk_neutral_20260929 | 15_candidates_7_groups_68084_eval_rows_option_transport_skill_2.751pct_guard_failed_keep_incumbent_136_tests_no_trading |
| A0612 | execute | cdf_tail_constrained_quantile_blend | 2026-09-30T16:14:41+00:00 | configs/run-predictive-cdf-tail-blend.json | docs/memos/2026-09-30-tail-constrained-quantile-blend.md | pipeline_runs/predictive_cdf_tail_blend_20260930 | three_candidates_complete_68084_eval_rows_fine_tail_skill_1.307pct_development_SPY_lower_guard_failed_keep_incumbent_Luna_C0_M0_no_trading |
| A0613 | execute | cdf_guard_aware_selection | 2026-09-30T18:13:42+00:00 | configs/run-predictive-cdf-guard-aware.json | docs/memos/2026-09-30-guard-aware-cdf-selection.md | pipeline_runs/predictive_cdf_guard_aware_20260930 | very_conservative_passed_all_six_development_guards_dev_skill_1.122pct_later_skill_0.816pct_intervals_positive_promote_offline_research_incumbent_no_trading |
| A0614 | research | ADR-0196 center-only conditioned option transport | 2026-09-30T18:57:23+00:00 | ADR-0195 immutable panel; entry-known option surface, index, requested horizon and lagged features | Phase A/B guarded refusals; Phase C frozen center-only transport; standard report and standalone memo |  | Development +0.101% with identical six tail rates; reused 2019-2025 +0.0146% versus incumbent with intervals crossing zero. Offline research only. |
| A0615 | research | ADR-0197 point-in-time tail-data feature ablation | 2026-09-30T20:17:18+00:00 | ADR-0196 incumbent; local OHLC, raw option chain and prior-date Cboe archives | Five-family development search; guarded refusal; standalone memo and review evidence |  | VRP +0.270% development equal-cell CRPS versus incumbent but failed QQQ/SPY upper-tail guards; all candidates refused; later partitions unopened; IWM dividends remain null and strategy-ineligible; no trading |
| A0616 | research | cdf-gpd-decision-region-screen | 2026-09-30T23:07:54+00:00 | configs/run-cdf-gpd-decision-regions-screen-r4.json | docs/memos/2026-09-30-gpd-decision-region-screen.md |  | Fixed 60-entry development screen; date-only raw-grid diagnostic; no model or feature promotion. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
