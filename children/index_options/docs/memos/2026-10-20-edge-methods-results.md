# ADR-0270 ten edge methods against the market (2026-10-20)

## TL;DR

Combining the model with the market beats the market. The best method (M08, a four-way stack per DTE week) beats the **recalibrated market by +0.48% twCRPS (t 8.4)** and the raw market by +0.59% (t 2.8) on 2020-23 validation. It wins in every DTE week, in every strike zone, and in 2020, 2021 and 2023. It ties in 2022 (-0.05%) and loses slightly in the pre-crash weeks of early 2020 (-0.24%). Its gate is well above luck: **215 of 831 (ticker, expiry rank) cells pass against the recalibrated market, against 29 expected by luck (95th percentile 39)**. The five linear pools (M02-M06) are close behind (+0.30% to +0.37%, t 6 to 7). Three methods do not help: recalibrating the model alone (M07, -0.84%), LightGBM on the market (M10, -1.48%), and picking model or market per ticker (M09, -0.04%). These are small edges on a predictive score; no P&L is claimed.

## What ran

- Base: the R1 walk-forward rows (TCN chain arm, shared head, seed 11, yearly retrains). The model, the raw and recalibrated market, the pooled empirical and the per-name baseline each give a CDF on the 13-point z grid.
- New dskit node `GridCDFMethod` (`dskit/pipeline/libs/cdf_grid_methods.py`) builds one new forecast per fold from those CDFs. Each method is one JSON, `configs/run-edge-method-NN-*.json`.
- **Causal learning:**
  - Methods that use the model learn only from earlier years' validation rows that settled before the target year starts.
  - Market-only methods learn on the fold's own training window.
  - The 2019 warm-up has no earlier evidence, so it keeps the model and is never ranked (A0008).
- **Scores:**
  - A0002 twCRPS on the grid.
  - Region Brier on the 14 grid regions, grouped into five strike zones: left wing z <= -1.5, left shoulder -1.5..-0.5, body -0.5..0.5, right shoulder 0.5..1.5, right wing > 1.5.
  - The A0007 gate per (ticker, expiry rank).
  - Edges are measured against the raw market, the recalibrated market (one 21-knot PIT map per fold) and the R1 model. Positive means the method has the lower loss. SE is the larger of date and ticker clustering.
- **Rows:** 449,498 market-eligible validation rows, 2020-23.
- **Runtime:** the methods take 10-15 s each on CPU, and M10 takes 2 min.

| # | Method | What it does |
|---|---|---|
| M01 | market PIT map per DTE week | recalibrates the market separately for each DTE week |
| M02 | pool, global | w x model + (1-w) x recalibrated market |
| M03 | pool per DTE week | one weight per DTE week |
| M04 | pool per strike threshold | one weight per grid point, made monotone |
| M05 | pool per regime | one weight per tercile of the market's implied/realized dispersion |
| M06 | pool per ticker | per-ticker weight shrunk to the global one |
| M07 | model PIT map | recalibrates the model alone |
| M08 | four-way stack per DTE week | model, recalibrated market, pooled empirical and per-name baseline on the simplex |
| M09 | best per (ticker, DTE week) | model or recalibrated market, by earlier evidence |
| M10 | LightGBM on the market | monotone distributional classifier on the market curve plus 33 panel features |

## 1. Overall (validation 2020-23, twCRPS edge %, t in brackets)

| Method | vs raw market | vs recalibrated market | vs R1 model | In-sample vs recalibrated |
|---|---|---|---|---|
| M01 | +0.10 (0.5) | -0.02 (-0.6) | +0.11 | +0.10 |
| M02 | +0.48 (2.3) | +0.36 (7.3) | +0.49 | +1.18 |
| M03 | +0.47 (2.2) | +0.35 (6.9) | +0.48 | +1.18 |
| M04 | +0.42 (1.9) | +0.30 (5.7) | +0.43 | +1.20 |
| M05 | +0.45 (2.1) | +0.34 (6.9) | +0.46 | +1.18 |
| M06 | +0.48 (2.3) | +0.37 (7.3) | +0.49 | +1.18 |
| M07 | -0.72 (-2.1) | -0.84 (-4.3) | -0.71 | +1.30 |
| **M08** | **+0.59 (2.8)** | **+0.48 (8.4)** | **+0.60** | +1.36 |
| M09 | +0.07 (0.4) | -0.04 (-1.0) | +0.09 | +0.45 |
| M10 | -1.36 (-5.1) | -1.48 (-8.7) | -1.35 | +5.98 |

- The pools and the stack win because the model and the market make different errors. The learned model weight is 0.28-0.46 by fold (M02).
- Against the raw market the t-values are lower (about 2-3) because the raw market's own errors vary more by date.
- Per-DTE, per-ticker and per-regime weights add almost nothing over one global weight (M03, M05 and M06 are within 0.02 of M02). The extra pieces of M08, the pooled empirical and the baseline, add 0.12.
- M10 fits its training window well (+6% in sample) and loses out of sample. The same pattern sank M07: last year's miscalibration does not carry to the next year.

## 2. By year and regime (vs recalibrated market, %)

| Method | 2020 | 2021 | 2022 | 2023 | S0 pre-crash | S1 COVID | S2 recovery | S3 rate shock |
|---|---|---|---|---|---|---|---|---|
| M02 | +0.41 | +0.66 | +0.01 | +0.37 | -0.14 | +0.65 | +0.63 | +0.01 |
| M06 | +0.41 | +0.66 | +0.01 | +0.38 | -0.14 | +0.65 | +0.63 | +0.01 |
| M08 | +0.54 | +1.05 | -0.05 | +0.37 | -0.24 | +0.79 | +0.98 | -0.05 |
| M07 | -0.63 | -0.02 | -2.67 | +0.03 | -6.36 | -0.73 | +0.73 | -2.67 |
| M10 | -1.10 | -1.45 | -2.98 | -0.30 | -2.97 | +1.32 | -1.52 | -2.98 |

The 2022 rate shock is still a tie at best, and the calm pre-crash weeks before the COVID crash remain the weak spot.

## 3. By DTE (vs recalibrated market, twCRPS %)

| Method | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| M01 | +0.69 | +0.30 | +0.05 | -0.09 | -0.16 | -0.40 | -0.39 | -0.24 |
| M02 | +0.59 | +0.23 | +0.25 | +0.31 | +0.35 | +0.46 | +0.59 | +0.09 |
| M08 | +0.52 | +0.29 | +0.34 | +0.41 | +0.47 | +0.64 | +0.99 | +0.10 |

- The edge grows with DTE and peaks at 36-49 days: +0.64% to +0.99% for M08.
- M01 shows the market's miscalibration differs by horizon. A DTE-specific map helps the first week (+0.69%) and hurts beyond 3 weeks.
- Monthly-only names gain little: M08 by position runs -0.4% to +0.3%, and only position 1 is significant (+0.33%, t 2.5). They make up 4 of the 215 gate passes.

## 4. By strike zone (M08 vs recalibrated market, zone Brier edge %)

| DTE | Left wing | Left shoulder | Body | Right shoulder | Right wing |
|---|---|---|---|---|---|
| 1-7 | +0.40 | +0.42 | +0.44 | +0.55 | +0.80 |
| 8-14 | +0.32 | +0.32 | +0.27 | +0.43 | +0.68 |
| 15-21 | +0.42 | +0.39 | +0.36 | +0.61 | +0.80 |
| 22-28 | +0.66 | +0.52 | +0.44 | +0.79 | +0.97 |
| 29-35 | +0.74 | +0.70 | +0.55 | +0.88 | +1.18 |
| 36-42 | +0.95 | +0.91 | +0.63 | +1.14 | +1.40 |
| 43-49 | +1.11 | +1.36 | +1.06 | +1.69 | +1.41 |
| 50-60 | +0.69 | +0.80 | +0.68 | +1.09 | +0.60 |

- The largest edges are in the **right shoulder and right wing at 4-7 weeks** (+1.1% to +1.7%). This is where the raw market's upper tail is too narrow (9% of outcomes above its 95th percentile). Every zone and DTE cell is positive.
- Zone Brier and twCRPS can disagree. M07 gains in every zone (+0.5% to +1.0%) while losing on twCRPS. The zones score region masses, and twCRPS scores the cumulative curve. A0002 makes twCRPS the decision score.

## 5. By ticker (M08 vs recalibrated market)

| Count | twCRPS per ticker | Per (ticker, DTE) twCRPS | Per (ticker, DTE, zone) |
|---|---|---|---|
| Cells | 110 | 855 | 4,275 |
| Edge positive | 81 | 610 | 3,592 |
| t > 2 | 62 | 343 | 2,033 |
| t < -2 | 11 | 67 | 94 |

- Strongest tickers: EOG +1.5% (t 13), KR +2.0%, CNC +1.7%, CME +1.6%, BMY +1.4%, GILD +1.4%, CVS +1.2%, KKR +1.5%.
- Strongest (ticker, DTE) cells with at least 300 rows: EOG 29-42 days (+2.0% to +2.3%, t 9), KR 15-49 days (+1.8% to +3.7%), CVS 22-42 days, BMY 22-28.
- Strongest zone cells: AVGO left wing at 29-42 days (+3.0% to +3.6%), KLAC left wing at 36-42 (+5.4%), BBY and BIDU right wing at 29-35 (+2.4% to +3.6%), CME right shoulder at 36-42 (+4.7%).
- Losers: ENPH, F, CMG, GE and CCJ at 29-49 days (-1.2% to -2.1%, t -3.5 to -4.4).
- **Gate (A0007) vs recalibrated market:**
  - M08 has 215 passes against 29 by luck. M02-M06 have 184-217 against about 45.
  - The raw-market gate: M08 134 against 49, and M02 127 against 57.
  - M08 passes by rank 1-8: 49, 34, 27, 20, 19, 25, 20, 21 (rank 1 leads because the market is miscalibrated there).
- The per-cell t counts are not adjusted for multiplicity. The counts against luck are the honest reading. Two-way clustering is approximated by the larger one-way SE.

## 6. Caveats

- One base model and one seed. The pool weight and the stack are learned on 1-4 earlier years, so 2020 rests on 2019 alone.
- The edges are small: +0.3% to +1% of the twCRPS loss. They are tested on the 13-point strike grid, not on listed strikes.
- The recalibrated market is itself learned from the training window; a better market recalibration (for example per DTE in the tails) could absorb part of the edge. M01 shows that a naive per-DTE map does not.
- Holdout 2024-25 not read; 2026 sealed. Predictability only.
- **The evidence folder is over its cap.** The scores/ folder is 10.4 GB, above the 8 GB ceiling. The method outputs are about 150 MB each, not the 60 MB estimated. One superseded set without zone tables is moved aside, not deleted, and was verified bit-identical in forecasts. C: has 53 GB free.

## 7. What this means for production

This is the first configuration that beats the market overall and passes the gate well above luck: the model plus the recalibrated market plus the empirical baselines, combined per DTE week. Production follows the existing path: a second seed for the base model, then the 2024-25 holdout once for M08 and M02, then registration and serving. Serving it needs only the four CDFs and a small weight table per DTE week. The places to look for use are 4-7 week expiries, the right tail, and names like EOG, KR, CVS and AVGO.

## Files

`data/2026-10-20-edge-methods/` holds long CSVs, one row per method and cell (`edge_method` names the method):

- `edge_windows`: fold, year, period, pooled and in-sample, twCRPS and CRPS13, against three comparators.
- `edge_ticker_dte_zone`: per ticker, DTE week and strike zone, against the raw and recalibrated market.
- `edge_ticker_dte`: per ticker and DTE week, twCRPS.
- `edge_ticker_zone`: per ticker, twCRPS, CRPS13 and zones.
- `edge_dte_zone_region`: per DTE week, the 14 regions and 5 zones.
- `edge_gate_vs_market` and `edge_gate_vs_calibrated_market`: per (ticker, expiry rank).
- `edge_gate_summary`, `edge_perf_by_rank`, `edge_calibration` and `edge_cell_counts`.

The per-(ticker, DTE, 14-region) tables, the forecasts and the learned states (`fit.json`) are under the pilot root `scores/edge-method-NN-*/`. The gate tables come from the walk-forward `analysis_wf.py` run with `--method-rows`.
