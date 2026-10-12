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
| A1130 | execute | Amend T2 dispersion-IC test after review: partial rank IC given the straddle | 2026-10-11T02:58:36+00:00 | ADR-0259 amendment; review of the first build (c0924c4); configs/run-forecast-dispersion-ic.json | configs/run-forecast-dispersion-ic.json (hash 1ad8d730, 17 nodes); dskit ordering.partial_spearman + control column; CrossSectionIC control_field, HAC guard, int knobs |  | The note's ratio form is Pearson's spurious ratio correlation (noise forecast t = 7.2 in a dry run); replaced by the partial Spearman of forecast sd with \|move\| given the straddle ratio, recorded before any run. Field names fixed (attachments write unprefixed), spot-null and same-rows filters added; dry run on synthetic rows scores 20 stamps. Still not run. |
| A1131 | execute | forecast-dispersion-ic | 2026-10-11T03:14:40+00:00 | forecast-dispersion-ic | /home/russell/data/index_options/production-universe-rerun-20261008/forecast-dispersion-ic/forecast-dispersion-ic-2025-12-31-64c466f0 | /home/russell/data/index_options/production-universe-rerun-20261008/forecast-dispersion-ic/forecast-dispersion-ic-2025-12-31-64c466f0 | state=error hash=64c466f0 asof=2025-12-31 |
| A1132 | execute | T2 dispersion-IC run (ADR-0259): REFUSED at with_forecast, null spot | 2026-10-11T03:15:17+00:00 | configs/run-forecast-dispersion-ic.json (1ad8d730); condor-replay-inputs-20261009-v5; origin/main d7d309d; run A1131 | docs/memos/2026-10-10-t2-dispersion-ic-refusal.md |  | No IC produced; T2 neither passed nor failed. with_forecast: row 351 ('ANET','2024-11-05') spot 405.12 on stream, None in forecast_scale table (99,225 rows). Not a basis mismatch: a null spot on the forecast side. Nothing patched or re-run. Needs an owner-approved amendment (new pre-registered change) before any rerun. |
| A1133 | execute | scratch-coverage | 2026-10-11T03:45:16+00:00 | scratch-coverage | /home/russell/wt/index-options-dispersion-ic-20261010/children/index_options/pipeline_runs/scratch-coverage-2025-12-31-07f4302a | /home/russell/wt/index-options-dispersion-ic-20261010/children/index_options/pipeline_runs/scratch-coverage-2025-12-31-07f4302a | state=ran hash=07f4302a asof=2025-12-31 |
| A1134 | execute | forecast-dispersion-ic | 2026-10-11T03:48:06+00:00 | forecast-dispersion-ic | /home/russell/data/index_options/production-universe-rerun-20261008/forecast-dispersion-ic/forecast-dispersion-ic-2025-12-31-a3f9db74 | /home/russell/data/index_options/production-universe-rerun-20261008/forecast-dispersion-ic/forecast-dispersion-ic-2025-12-31-a3f9db74 | state=ran hash=a3f9db74 asof=2025-12-31 |
| A1135 | execute | T2 dispersion-IC rerun (ADR-0259 amd 2): FAILS, IC 0.033 t 1.15 | 2026-10-11T03:48:51+00:00 | configs/run-forecast-dispersion-ic.json (c3a202f0); condor-replay-inputs-20261009-v5; run a3f9db74 | docs/memos/2026-10-10-t2-dispersion-ic-result.md; ADR-0259 amendment 2 |  | beyond_market IC 0.0331 t 1.15 n_stamps 91 (need t>=2.5): FAIL. forecast_alone 0.411 t 22.0; implied_alone 0.453 t 29.2. Entry phase 0.086 t 2.20, calibration -0.014 t -0.36. Fix after the A1131 refusal: forecast_priced spot>0 filter (80.7% of forecast rows have null spot by the replay basis gate); 155/5723 straddle rows absent, guard 0.1 unchanged. Premium program with forecast as risk filter is the branch; dispersion book not justified. |
| A1136 | execute | equity-condor-2024-holdout-lowcredit | 2026-10-12T01:08:17+00:00 | equity-condor-2024-holdout-lowcredit | /home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-2024-holdout/equity-condor-2024-holdout-lowcredit-2025-12-31-a5fe410c | /home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-2024-holdout/equity-condor-2024-holdout-lowcredit-2025-12-31-a5fe410c | state=ran hash=a5fe410c asof=2025-12-31 |
| A1137 | execute | 2024 holdout: low credit/wing condor passes, but no gap vs rest | 2026-10-12T01:31:27+00:00 | configs/run-equity-condor-2024-holdout.json (a5fe410c); rule credit/wing<0.15 preregistered in notes | run condor-replay-run-2024-holdout a5fe410c |  | 2024 entry 2024-07-30..11-26, nominal, 445 trades 61 names. Rule cell n=52 names=22 win 73.1% vs BE 35.1% +306/trade, name-boot90 [115,643]: pre-registered pass. BUT whole 2024 book +196/trade (bull market); same-date gap low-minus-rest +26/trade [-190,250] in 2024, +224 [-28,464] in 2025. Pooled lowest quintile date-demeaned +190 [35,335], 2024 -34. Lead only, not edge. 2024 inspected: development evidence. |
| A1138 | execute | Pattern search (4 agents) on v2 replay: nothing survives adjustment | 2026-10-12T01:31:27+00:00 | trades.json 1259 nominal 2025 trades; rowscores; centers | none (scratch analysis) |  | Entry features 1150 cells best adj p 0.31; regimes 66 cells best p 0.22 (P&L is market direction, date corr 0.62); structures 720 rules best p 0.235 (call spread low credit/wing); forecast shape worse than crude proxy. Book breakeven win 59.4% vs 59.0%. Sizing by baseline skill loses (-126k vs -8k, MSTR/TTD driven). Baseline-skill gate 32 names: only OXY/AMC traded (+1727, 40 trades). |
| A1139 | execute | Real option-implied CDF vs forecast: tie on CRPS; per-ticker gate fails | 2026-10-12T01:31:27+00:00 | OptionPriceCDF (ADR-0213) on chain VWAP, min_wing_nodes 3 max_inner_gap .08 max_outer_gap .12; 4299 scored rows | none (scratch analysis) |  | Curve built for 2121/4299 rows (66 names): wing support 1625, fwd pairs 346, strikes 207. CRPS market .0724 forecast .0720, diff +.0004 CI by symbol [-.0008,.0016] by date [-.0012,.0020]; PIT KS forecast .033 vs implied .107 (implied PIT mean .565). Supersedes crude N(0,s^2) proxy claims that forecast is worse than market. Per ticker: 2024 gate t>1.645 8 of 39 (null 2), BH admits 7, but 2025 reverses (Spearman -0.36 p .024); all-rows BH admits 0. Risk-neutral not physical, VWAP not quotes, liquid half only. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
