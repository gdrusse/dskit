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
| A0620 | execute | causal-decision-region-calibration-and-robust-condor | 2026-09-30T23:55:00+00:00 | configs/run-cdf-gpd-decision-regions-calibration.json; configs/run-cdf-causal-robust-condor-calibration.json | docs/memos/2026-09-30-causal-decision-region-calibration-and-robust-condor.md | pipeline_runs/cdf_gpd_decision_regions_calibration_r3_20260930; pipeline_runs/cdf_causal_robust_condor_calibration_r3_20260930 | 180_forecasts_41061_candidates_4_corrections_all_rejected_keep_GPD_correction_selection_excluded_from_rolling_radius_history_26_final_rows_3_trades_2_robust_no_trade_21_unsupported_mean_pnl_minus_0.139661_per_share_no_trading |
| A0621 | research | decision-region-signal-synthesis | 2026-09-30T23:59:00+00:00 | Consolidated CDF evidence and signal-first investigation path | docs/memos/2026-09-30-decision-region-signal-synthesis.md | docs/memos/2026-09-30-decision-region-signal-synthesis.md | branches_consolidated_ADR0198_to_ADR0206_actual_wing_signal_weak_option_transport_signal_strong_but_tail_guard_failed_next_four_candidate_actual_wing_comparison |
| A0622 | execute | actual-listed-wing-paired-audit | 2026-09-30T23:59:30+00:00 | configs/run-cdf-actual-wing-paired-audit.json; configs/run-cdf-actual-wing-paired-audit-r2.json; configs/run-cdf-actual-wing-paired-audit-r3.json | docs/memos/2026-09-30-actual-listed-wing-paired-audit.md | pipeline_runs/cdf_actual_wing_paired_audit_r3_20260930 | r1_quadrature_refusal r2_serialization_refusal r3_complete forecasts=1992 dates=249 chain_rows=344932 candidate_rows=1761423 unique_wings=67073 option_transport_wing_skill=1.207pct interval_crosses_zero qqq=2.314pct spy=0.100pct no_model_promotion no_optimizer_tuning |
| A0623 | execute | decision-region-loss-zoo | 2026-09-30T23:59:45+00:00 | configs/run-predictive-cdf-decision-loss-zoo.json through run-predictive-cdf-decision-loss-zoo-r5.json | docs/memos/2026-09-30-decision-region-loss-zoo-results.md | pipeline_runs/predictive_cdf_decision_loss_zoo_r5_20260930 | six_models 32588_panel_rows 5917_eligible_later_forecasts 251_dates wide_mlp_local_skill=1.173pct intervals_cross_zero spy=2.396pct qqq=-4.376pct all_global_crps_worse no_promotion terra_C0_M0 runtime=4m22s rss=3.21GiB |
| A0624 | execute | spy-decision-region-persistence | 2026-10-01T00:56:00-04:00 | configs/run-predictive-cdf-decision-loss-spy-persistence.json | docs/memos/2026-10-01-spy-decision-region-persistence.md | pipeline_runs/predictive_cdf_decision_loss_spy_persistence_20261001 | frozen_raw_wide_mlp 23141_eligible 2020_2025 local_skill_positive_5_of_6_years 30d_lo=-0.223pct_fail 60d_lo=0.020pct_pass all_global_tail_coverage_guards_pass no_promotion runtime=4m20s rss=3.27GiB terra_C0_M0 |
| A0625 | execute | qqq-decision-strike-diagnosis | 2026-10-01T05:17:00-04:00 | configs/run-decision-strike-qqq-diagnosis.json | docs/memos/2026-10-01-qqq-decision-strike-diagnosis.md | pipeline_runs/decision_strike_qqq_diagnosis_20261001 | frozen_raw_wide_mlp 1676_eligible 90795_thresholds 251_dates overall_skill=-4.376pct calls=-3.262pct puts=-7.373pct intervals_cross_zero downside_probability_overpredicted no_promotion no_optimizer runtime=12.6s rss=0.77GiB terra_C0_M0 |
| A0626 | research | stock-options/2026-10-01-source-and-starting-symbol | 2026-10-01T07:47:40+00:00 | Choose first stock-options baseline | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md |  |
| A0627 | research | stock-options/2026-10-01-source-and-starting-symbol-r2 | 2026-10-01T12:29:07+00:00 | Correct multiplier and target-window evidence | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md |  |
| A0628 | research | stock-options/2026-10-01-source-and-starting-symbol-r3 | 2026-10-01T13:49:03+00:00 | Correct contract-accounting and 44-DTE provenance | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md |  |
| A0629 | research | stock-options/2026-10-01-source-and-starting-symbol-r4 | 2026-10-01T14:19:53+00:00 | Separate stock forecast training from option action-set testing | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md | docs/research/stock-options/2026-10-01-source-and-starting-symbol.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
