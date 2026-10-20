# ADR-0270 adjustments: shared head, regularization, design inputs (2026-10-20)

## TL;DR

The adjustments close the gap to the market. Validation 2020-23, twCRPS edge vs the raw market goes from **-0.67% (t -2.2) to -0.01% (R1) and +0.11% (R2)**: a tie, no longer a loss. Against the recalibrated market, the harder bar, it goes from **-0.78% (t -3.1) to -0.13% (R1) and -0.01% (R2)**. The model still does not beat the market overall. It now ties it, beats the per-name baseline by about 2%, and beats the pooled empirical by about 1% (t 6 to 8). Cell-level passes rise well above luck (R1 120 vs 69 expected against the recalibrated market).

## What changed (same folds, seed 11, TCN chain arm, patience 50, ceiling 150)

| Run | Change | Parameters |
|---|---|---|
| main | 116 ticker heads, wd 0.01, dropout 0.1, lr 3e-4 | 12.3k |
| lr1e4 | main with lr 1e-4 | 12.3k |
| R1 | one shared head (no ticker heads), wd 0.3, dropout 0.2 | 3.0k |
| R2 | R1 plus vol-unit returns, 13 logit differences of the market curve, 2 regime scalars (81 inputs) | 3.1k |

Comparators, rows and score are as in the first memo (`2026-10-20-walkforward-results.md`). Recalibrated market = the raw market after a per-fold 21-knot probability-integral-transform map fitted on that fold's training window only. Edge = (comparator - model) / comparator; positive = model better.

## 1. Performance, validation 2020-23 (market-eligible rows, twCRPS edge %)

| Comparator | main | lr1e4 | R1 | R2 |
|---|---|---|---|---|
| Raw market | -0.67 | -0.64 | -0.01 | **+0.11** |
| Recalibrated market | -0.78 | -0.76 | -0.13 | **-0.01** |
| Per-name baseline | +1.28 | +1.30 | +1.92 | **+2.04** |
| Pooled empirical | +0.37 | +0.40 | +1.02 | **+1.14** |
| In-sample (training windows) vs raw market | +4.19 | +4.38 | +2.86 | +2.21 |

- Standard errors: vs raw market 0.30 / 0.31 / 0.23 / 0.21; vs recalibrated 0.25 / 0.26 / 0.13 / 0.10. R1 and R2 are statistically level with both markets (|t| < 1).
- The in-sample minus out-of-sample gap shrinks from 4.9 points (main) to 2.9 (R1) and 2.1 (R2): less overfit.
- Other metrics vs the raw market (R1 / R2): plain CRPS -0.03 / +0.05, tail CRPS +0.02 / +0.19, strike Brier +0.37 / +0.24, wing twCRPS +0.31 / +0.21 (main: -0.72, -0.57, -0.44, -0.48). All five metrics move together.

## 2. By year and regime (edge vs the recalibrated market, %)

| Window | main | R1 | R2 |
|---|---|---|---|
| 2020 | -0.72 | +0.19 | -0.23 |
| 2021 | +0.13 | -0.07 | +0.47 |
| 2022 | -1.74 | -0.83 | -0.35 |
| 2023 | -0.79 | +0.25 | +0.08 |
| S0 pre-crash (Jan-Feb 2020) | -0.81 | -1.05 | -0.72 |
| S1 COVID crash | -2.42 | +0.70 | -0.80 |
| S2 recovery | +0.22 | +0.10 | +0.45 |
| S3 rate shock (2022) | -1.74 | -0.83 | -0.35 |
| S4 2023 | -0.79 | +0.25 | +0.08 |

- The 2022 rate-shock loss is cut by 80% in R2 (-1.74 to -0.35). The pre-crash weeks (S0) remain the weakest window in every run.
- R2's regime scalars and market logit differences help the calm and rate-shock windows (S2, S3, S4); R1 does better in the COVID crash (S1), so the regime information is not uniformly useful.
- Versus the raw market the same pattern holds (R2: 2020 +0.50, 2021 +2.54, 2022 -2.93, 2023 +0.26).

## 3. By expiry rank (weekly names, edge vs recalibrated market, %)

| Rank | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| main | -1.48 | -1.31 | -1.00 | -0.71 | -0.45 | -0.08 | -0.03 | +0.55 |
| R1 | +0.02 | -0.44 | -0.29 | -0.13 | 0.00 | +0.31 | 0.00 | +0.49 |
| R2 | -0.21 | -0.36 | -0.13 | -0.01 | +0.05 | +0.30 | +0.27 | **+1.02** |

The short-horizon loss to the market is mostly gone; the model leads at ranks 5-8 (about 4 to 8 weeks). The 26 monthly-only names improve most under R2 (recalibrated-market edge -0.4% to +0.1% across positions 1-8, against -2.0% to -3.6% in main), where R1 stays at -0.3% to -1.8%. Per-name baseline edge is positive at every rank in every run (R2: +1.5% to +2.8%).

## 4. How the models train

| Run | Epochs per fold | Best epoch per fold (1-5) | Train loss first to last (fold 2) | Monitor best vs epoch 0 (fold 2) |
|---|---|---|---|---|
| main | 51-52 | 1, 2, 1, 2, 2 | 1.029 to 0.843 | 0.903 vs 0.946 |
| R1 | 51-73 | 23, 1, 20, 1, 2 | 1.020 to 0.986 | 0.882 vs 0.902 |
| R2 | 51 (folds 2-5) | 1, 1, 1, 1 | 1.019 to 0.988 | 0.881 vs 0.957 |

- With a shared head and strong regularization the training loss barely falls (about 3%, against 18% in main) and the monitor loss no longer climbs sharply: the memorization is gone. R1 trains longer in the 2019 and 2021 folds (best epochs 23 and 20), so it is still learning there.
- R2's best checkpoint is epoch 1 in every fold: the model reaches its best monitor loss after one pass and further epochs do not improve it. **Caveat:** R2's warm-up fold (2019, never ranked) was skipped by the fit queue because patience 50 was not exhausted within 150 epochs. R2 therefore has folds 2-5 only, which are the ranked folds; no result here depends on fold 1.
- The scored model is the best-monitor checkpoint. For R2 that is a one-epoch model, so part of its gain may be the smoothness of the shared head rather than learned structure. The next check is a second seed.

## 5. Calibration (validation 2020-23; share below the 5th and above the 95th percentile, nominal 5%)

| Forecaster | Below 5% | Above 95% |
|---|---|---|
| main | 7.9 | 5.9 |
| R1 | 7.9 | 5.7 |
| R2 | **6.6** | 5.6 |
| Raw market | 4.7 | 9.0 |
| Per-name baseline | 8.5 | 10.1 |
| Pooled empirical | 4.8 | 4.7 |

R2's lower tail is the closest to nominal of the three models. The raw market's upper tail (9.0%) is the miscalibration the recalibrated market removes.

## 6. By ticker and expiry (gate A0007: beat the market and the per-name baseline in at least 3 of 4 years and 3 of 5 regimes)

| Market used | main | R1 | R2 |
|---|---|---|---|
| Raw market: cells passing / expected by luck (95th pct) | 75 / 76 (89) | **108 / 68 (81)** | **89 / 62 (75)** |
| Recalibrated market | 93 / 80 (94) | **120 / 69 (83)** | **94 / 64 (77)** |

- Of 831 cells, R1 and R2 exceed their luck 95th percentile on both markets; main exceeds it on neither. 67 cells pass in both R1 and R2 against the recalibrated market, 12 in all three runs.
- Leads that pass in R1 and R2 with at least 300 dates (edge vs recalibrated market, R1 / R2): EOG ranks 4-7 (+2.0% to +4.7%), KR ranks 4-7 (+2.3% to +4.2%), APA ranks 4 and 6, DAL ranks 1, 5, 7, BMY 7, AKAM 1, DIS 1, CNC 7, COP 6, BBY 6, EPD 6. The energy and grocery names differ from the first run's leads (AVGO, KKR, CMG), which a one-seed screen cannot settle: treat both lists as leads.
- No multiplicity correction (A0007). Power remains about 120-200 independent expiries per cell, so a single cell is not a finding.

## 7. Caveats

- One seed (11). A second seed and a no-feature control would show whether R2's +0.11% and R1's pass counts are above the noise between seeds.
- Production comparator is the recalibrated market, built here in the analysis stage and not yet in the scorer.
- R1 and R2 change two things each; the ladder does not separate shared head from regularization (R1) or the three new inputs from each other (R2).
- Holdout 2024-25 not read. Predictability only: no P&L.

## 8. What this means

The overfit diagnosed in the first memo was real and the cheapest fixes work: removing per-ticker heads and raising regularization turned a significant loss to the market into a tie and a clear win over both baselines. Not yet a production candidate, since nothing beats the recalibrated market overall. The cells that do pass (120 of 831 for R1) justify the next step: a second seed and the residual arm on the lead cells, then the holdout for cells that survive.

## Files

`data/2026-10-20-adjustments/`: `comparison_*` (all four runs side by side: performance by window, by rank, gate summary, training summary) and the full per-run set for R1 and R2 (`wf_r1_*`, `wf_r2_*`: training curves, performance by window and rank, 4,555-row cell table, gate per ticker and rank against raw and recalibrated market, calibration). `data/2026-10-20-walkforward/` is refreshed with the recalibrated-market rows and two new gate files per run. Pilot root `/home/russell/data/index_options/theta-strike-screen-20261018`: `fits/wf-r1`, `fits/wf-r2`, `scores/strike-region-scores-wf-r1`, `-wf-r2`, panel-v2 (`panel-v2/frame.parquet`). Documents: resolved R1/R2 fit and score documents under `documents/study/`; the generator is `prepare/walkforward/wf_prep3.sh`.
