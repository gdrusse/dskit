# ADR-0270 edge methods round 2: predictive difference from the market and baselines (2026-10-20)

## TL;DR

The best method is now **M16: a weighted average of the forecasters' quantile functions, with one weight set per DTE week**. On 2020-23 validation it beats:
- the **recalibrated market by +0.55% twCRPS (t 9.6)**;
- the raw market by +0.66% (t 3.2);
- the per-name baseline by +2.6% (t 12.8);
- the pooled empirical by +1.7%.

It is the **first method that is positive in every regime**: pre-crash +0.05%, COVID crash +0.93%, recovery +1.00%, 2022 rate shock +0.10%, 2023 +0.35%. It passes the A0007 gate in **238 of 831 cells, against 28 expected by luck**.

Refitting the stack's weights every 21 days as outcomes settle (M11, M12) also turns 2022 positive (+0.04% to +0.11%), at +0.49% to +0.50% overall.

**Seed check passed:** the round-1 stack on R1 seed 29 (M20) scores +0.49% over 2021-23, the same as seed 11 (+0.46%).

Beta pools, six-model stacks and per-threshold weights do not help. Everything here is a predictive score difference; nothing is about trading.

## What ran

- **Base inputs:** the same R1 rows, comparators and causal learning as round 1 (memo `2026-10-20-edge-methods-results.md`). Round 1 was rerun with the per-name and pooled baselines added as comparators; its forecasts are bit-identical to before.
- **Node additions** (reviewed by three skeptic lenses; the one Major was a test gap, now closed):
  - refits every N days on rows settled before each block;
  - recency weights;
  - the beta-transformed pool;
  - quantile averaging;
  - forecasters joined from another run.
- **Second seed:** R1 with seed 29 was fitted on the GPU (CUDA graph, 22 min fit and 20 min scoring).
- **Warm-up folds:** seed 29's and R2's 2019 warm-up fold was skipped by the fit queue (loss still improving at 150 epochs). Methods that need 2019 to learn 2020 weights fall back to the base model in 2020: M18, M20, and the first weeks of M19. Use their 2021-23 numbers.

| # | Method |
|---|---|
| M11 | four-way stack (model, recalibrated market, pooled, per-name baseline), refitted every 21 days |
| M12 | M11 with one weight set per DTE week |
| M13 | M08 with learning rows weighted by a one-year half-life |
| M14 | beta-transformed pool of the four |
| M15 | M14 refitted every 21 days |
| M16 | quantile average of the four, per DTE week |
| M17 | four-way stack per DTE week and per strike threshold |
| M18 | six-way stack per DTE week (adds R1 seed 29 and the first-run per-ticker-head model) |
| M19 | M18 without DTE weights, refitted every 21 days |
| M20 | M08 recipe on R1 seed 29 (seed check) |

## 1. Overall, all 20 methods (validation, twCRPS edge %, t in brackets; positive = method better)

| Method | vs raw market | vs recalibrated market | vs per-name baseline | vs pooled | 2021-23 vs recal. |
|---|---|---|---|---|---|
| M01 market PIT per DTE | +0.10 (0.5) | -0.02 (-0.6) | +2.03 (8.6) | +1.13 | -0.04 |
| M02 pool global | +0.48 (2.3) | +0.36 (7.3) | +2.40 (10.6) | +1.51 | +0.35 |
| M03 pool per DTE | +0.47 (2.2) | +0.35 (6.9) | +2.39 (10.6) | +1.50 | +0.33 |
| M04 pool per threshold | +0.42 (1.9) | +0.30 (5.7) | +2.34 (10.4) | +1.45 | +0.27 |
| M05 pool per regime | +0.45 (2.1) | +0.34 (6.9) | +2.38 (10.5) | +1.48 | +0.33 |
| M06 pool per ticker | +0.48 (2.3) | +0.37 (7.3) | +2.40 (10.6) | +1.51 | +0.35 |
| M07 model PIT | -0.72 (-2.1) | -0.84 (-4.3) | +1.22 (5.4) | +0.32 | -0.90 |
| M08 stack-4 per DTE | +0.59 (2.8) | +0.48 (8.4) | +2.51 (12.1) | +1.62 | +0.46 |
| M09 best per cell | +0.07 (0.4) | -0.04 (-1.0) | +2.01 (8.5) | +1.11 | -0.05 |
| M10 LightGBM | -1.36 (-5.1) | -1.48 (-8.7) | +0.60 (2.2) | -0.31 | -1.60 |
| M11 stack-4 rolling | +0.61 (2.9) | +0.49 (8.2) | +2.53 (12.5) | +1.64 | +0.49 |
| M12 stack-4 rolling per DTE | +0.62 (2.9) | +0.50 (7.8) | +2.54 (12.6) | +1.65 | +0.49 |
| M13 stack-4 recency | +0.55 (2.6) | +0.43 (7.1) | +2.47 (12.1) | +1.58 | +0.41 |
| M14 beta pool | +0.25 (1.0) | +0.13 (1.7) | +2.18 (11.0) | +1.28 | +0.01 |
| M15 beta pool rolling | -0.18 (-0.9) | -0.30 (-3.0) | +1.75 (8.4) | +0.85 | +0.24 |
| **M16 quantile average per DTE** | **+0.66 (3.2)** | **+0.55 (9.6)** | **+2.58 (12.8)** | **+1.69** | **+0.52** |
| M17 stack per DTE x threshold | +0.53 (2.3) | +0.42 (5.9) | +2.45 (12.0) | +1.56 | +0.24 |
| M18 stack-6 per DTE | +0.53 (2.4) | +0.41 (4.2) | +2.45 (13.6) | +1.56 | +0.49 |
| M19 stack-6 rolling | +0.42 (1.9) | +0.31 (3.2) | +2.35 (12.9) | +1.45 | +0.48 |
| M20 M08 on seed 29 | +0.57 (2.7) | +0.46 (5.5) | +2.50 (12.8) | +1.60 | +0.49 |

- Against the raw market the t-values are about 3, because the raw market's errors vary strongly by date.
- The beta pools overfit their shape parameters. M15 loses 3.2% in the COVID crash, refitting on a few weeks of extreme outcomes.
- More models (M18, M19) add nothing beyond the four-way stack once the 2020 fallback is set aside.

## 2. By time range (vs recalibrated market, twCRPS %)

| Method | S0 pre-crash | S1 COVID | S2 recovery | S3 rate shock 2022 | S4 2023 |
|---|---|---|---|---|---|
| M08 | -0.24 | +0.79 | +0.98 | -0.05 | +0.37 |
| M11 | -0.17 | +0.48 | +0.97 | +0.11 | +0.35 |
| M12 | -0.24 | +0.53 | +1.05 | +0.04 | +0.35 |
| **M16** | **+0.05** | **+0.93** | **+1.00** | **+0.10** | **+0.35** |
| M20 (seed 29) | -0.70 | +1.34 | +0.92 | -0.08 | +0.35 |

M16 by year and DTE week, vs recalibrated market:

| Year | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| 2020 | +0.65 | +0.29 | +0.30 | +0.40 | +0.53 | +0.92 | +1.46 | +0.77 |
| 2021 | +0.19 | +0.46 | +0.74 | +1.10 | +1.42 | +1.90 | +2.34 | +0.49 |
| 2022 | +1.01 | +0.37 | +0.26 | +0.05 | -0.22 | -0.42 | -0.17 | -0.41 |
| 2023 | +0.31 | +0.27 | +0.33 | +0.40 | +0.40 | +0.41 | +0.61 | -0.22 |

In 2022 the edge flips with DTE. It holds at 1-4 weeks and is lost beyond 4 weeks, where the rate shock repriced the longer expiries.

## 3. By DTE (all validation)

| vs | Method | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|---|
| recal. market | M08 | +0.52 | +0.29 | +0.34 | +0.41 | +0.47 | +0.64 | +0.99 | +0.10 |
| recal. market | M12 | +0.57 | +0.25 | +0.33 | +0.43 | +0.53 | +0.73 | +1.01 | +0.16 |
| recal. market | M16 | +0.55 | +0.35 | +0.41 | +0.49 | +0.54 | +0.72 | +1.08 | +0.18 |
| per-name baseline | M16 | +3.59 | +2.81 | +2.28 | +2.21 | +2.25 | +2.26 | +2.50 | +3.56 |

## 4. By part of the distribution (zone Brier edge %, all DTE pooled)

| Method | vs | Left wing | Left shoulder | Body | Right shoulder | Right wing |
|---|---|---|---|---|---|---|
| M08 | recal. market | +0.65 | +0.64 | +0.52 | +0.85 | +1.00 |
| M12 | recal. market | +0.72 | +0.67 | +0.55 | +0.87 | +1.05 |
| M16 | recal. market | +0.74 | +0.75 | +0.61 | +1.04 | +1.08 |
| M16 | per-name baseline | +0.79 | +0.89 | +3.79 | +2.07 | +1.27 |

- Against the market, the edge is largest on the upside: the right shoulder and right wing.
- Against the per-name baseline, the gain is mostly in the body (+3.8%). The baseline ignores the current volatility state, so it gets the centre wrong.
- The zones are a decomposition (A0002). The 14-region, per-DTE detail is in the CSVs.

## 5. By ticker (M16)

| Comparator | Tickers | Edge positive | t > 2 | t < -2 | Positive in all 4 years |
|---|---|---|---|---|---|
| Recalibrated market | 110 | 87 | 68 | 10 | 13 (55 in at least 3) |
| Per-name baseline | 110 | 96 | 84 | 5 | |

- **Leading tickers vs the recalibrated market** (at least 1,000 rows): EOG +1.4% (t 11.7), CME +1.6%, KKR +1.9%, CNC +1.7%, KR +1.5%, BMY +1.3%, CVS +1.2%, C +1.1%.
- **Lagging tickers:** FTNT -1.2% (t -6.6), ENPH -1.3%, CCJ -0.8%, GE -0.8%, CRM -0.4%.
- **Per (ticker, DTE week), M16 vs the recalibrated market:** 382 of 855 cells have t > 2, and 57 have t < -2. Per (ticker, DTE week, zone): 1,930 of 4,275 have t > 2, and 73 have t < -2. These counts are not adjusted for multiplicity.

**Gate A0007** (beat the market and the per-name baseline in at least 3 of 4 years and 3 of 5 regimes), passes over luck at the 95th percentile:

| Market | M08 | M12 | M16 | M20 |
|---|---|---|---|---|
| Recalibrated | 215 / 29 (39) | 193 / 29 (39) | **238 / 28 (38)** | 162 / 37 (49) |
| Raw | 134 / 49 (61) | 126 / 49 (61) | **141 / 50 (61)** | 149 / 50 (62) |

M20's 2020 cells are the base model, so its gate is conservative.

**Calibration** (validation; nominal 5% below the 5th and 5% above the 95th percentile): M16 is at 5.8% and 5.0%; M08 at 6.1% and 5.2%; the raw market at 4.7% and 9.0%.

## 6. What to focus on next

1. **Rolling quantile averaging.** Combine M16 (best family) with M12 (rolling weights, best in 2022). Weights per DTE week, refitted every 21 days. This is the leading design.
2. **Long-DTE expiries in rate shocks.** 2022 beyond 4 weeks is the one systematic loss. Add a regime input to the weights, such as recent change in implied volatility or rates, rather than more models.
3. **Fix the warm-up fold.** Raise the ceiling for the warm-up fold so seed 29 and R2 have 2019 rows, and every stack has learned weights for 2020.
4. **Leave these out:** beta pools, per-threshold weights, per-cell switches and model-only recalibration. Each lost or overfit here.

## 7. How this moves toward production

- **Candidate:** M16, possibly in the rolling variant. It is the first configuration with a positive edge over the recalibrated market in every year-regime window except one cell of 2022, with a gate count far above luck.
- **Robust to the seed:** the second seed (M20) reproduces the stack's edge.
- **Simple to serve:** four CDFs per row, then a table of quantile-average weights per DTE week, refitted on a schedule.
- **Still missing:**
  - the 2024-25 holdout, opened once for M16, the rolling M16 and M08;
  - the recalibrated market inside the scorer, not only in the analysis;
  - a serving document.
- Holdout and 2026 are untouched. All results are predictive scores; no trading or P&L has been evaluated.

## Files

The data folder is `data/2026-10-20-edge-methods/`, covering all 20 methods (`edge_method` names the method). Comparators are market, market_cal, baseline, pooled and model.

| File | Contents |
|---|---|
| `edge_windows` | fold, year, regime, pooled and in-sample, twCRPS and CRPS13 |
| `edge_ticker_dte_zone.csv.gz` | ticker x DTE week x strike zone |
| `edge_ticker_dte.csv.gz` | ticker x DTE week, twCRPS |
| `edge_ticker_zone.csv.gz` | ticker, all metrics and zones |
| `edge_ticker_year.csv.gz` | ticker x year |
| `edge_year_dte_zone`, `edge_period_dte_zone` | time range x DTE x zone |
| `edge_dte_zone_region` | DTE x the 14 regions and the 5 zones |
| `edge_gate_*` | A0007 per (ticker, expiry rank) |
| `edge_perf_by_rank`, `edge_calibration`, `edge_cell_counts`, `edge_fitted_share` | |

Documents are `configs/run-edge-method-01..20-*.json`. Per-region tables, forecasts and learned weights (`fit.json`) are under the pilot root `scores/edge-method-*`.
