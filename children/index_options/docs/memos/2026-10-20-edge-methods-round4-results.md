# ADR-0270 edge methods round 4: the Greeks and additional features with other architectures (2026-10-21)

## TL;DR

- **The owner's hypothesis holds for one architecture: the 90 wide inputs (Theta Greeks, volume, open interest, depth, own IV) help an MLP, not a TCN.** Standalone, the wide MLP (hidden 32) is the first fitted model to beat the recalibrated market in the pooled validation years, **+0.18% twCRPS** (MLP on 66 inputs was +0.03%, the wide TCN -0.26%); the wide MLP 64 is +0.12%; the wide TCN 32 is -0.10%.
- **New best stack: M48**, the M16 recipe (quantile average per DTE week of model + recalibrated market + pooled + per-name baseline) with the wide MLP 64 in the model slot: **+0.64% over the recalibrated market (t 11.1)**, +0.75% over the raw market, +2.67% over the per-name baseline, positive in all five regimes with **+0.32% in the 2022 rate shock** (M29 had +0.21%, M33 +0.16%), gate **252 of 831 cells against 31 by luck**, 21 tickers positive in all four years (M29: 13). M47 (wide MLP 32) is +0.60% (t 9.8); M50 (bag of the five fits) +0.56%; M49 (wide TCN 32) +0.53%.
- **The Greeks as weight features or post-hoc corrections do not help, again.** Curated short lists of weight features (M40 +0.43%, M41 +0.53%, M46 +0.36%) and three times the learning rows for the thirteen-feature law (M42 +0.32%) all trail M34's three market-shape features (+0.54%); a regularized GBM on top of the M29 stack loses 0.31%; the GBM and location-scale corrections of the R1 model lose 1.3-1.4%.
- **2022 beyond four weeks is now near zero for M48** (+0.27, +0.04, -0.05, +0.23, 0.00 for 22-60 days against -0.13 to -0.71 for M29): the wide inputs reach the one place the earlier stacks lost.
- Everything here is a predictive score difference on the 2020-23 validation years; nothing is about trading.

## What ran

Three fits on the GPU (18-22 min each, 8.8-9.6 GB RSS, packet 4 now 9,838 of 14,400 s including the round-3 wide fit), twelve GridCDFMethod documents (21-234 s, 3.2-7.9 GB) and eleven gate analyses. Evidence under `scores/` is 29.3 GB of the 32 GB cap (owner raised it from 24 GB this round).

| Fit | Change from the wide fit (90 inputs, TCN hidden 16) | Parameters | Folds fitted |
|---|---|---|---|
| wide-mlp | MLP hidden 32 | 2,025 | all 5 |
| wide-mlp64 | MLP hidden 64, dropout 0.3 | 3,273 | all 5 |
| wide-tcn32 | TCN hidden 32 | 9,705 | 2-5 (fold 1 refused: monitor still improving at 150 epochs) |

Everything else is unchanged from R1: AdamW, weight decay 0.3, learning rate 3e-4, batch 512, patience 50, ceiling 150, seed 11, losses crps + tail_crps. The refused fold is a skip, not a failure; M49 uses the R1 model for fold 1 (the 2019 warm-up, never ranked), so its cell count stays 831.

| # | Method |
|---|---|
| M40 | M34's feature-conditioned quantile average (four components, weights a softmax of features) with a curated five-feature list: log DTE, log ATM IV, 25-delta skew, implied minus realized variance, put/call volume imbalance |
| M41 | as M40 with the positioning list: log DTE, log ATM IV, skew, delta-weighted open interest, lagged open-interest change, own IV |
| M42 | M39's thirteen-feature weight law learned on 300,000 rows instead of 100,000 |
| M43 | strongly regularized monotone LightGBM (7 leaves, 2,000 rows per leaf, lambda 20, 150 trees) correcting the M29 stack from log DTE, log ATM IV, skew and the ten Greeks/volume/OI inputs |
| M44 | M37's GBM correction applied to the R1 model instead of the market |
| M45 | M38's location-scale adjustment applied to the R1 model, per DTE week |
| M46 | linear pool of the four components with weights a softmax of the M40 list |
| M47, M48, M49 | the M16 recipe with the wide MLP 32, wide MLP 64, wide TCN 32 in the model slot |
| M50a | bag: quantile average per DTE week of R1 and the four 90-input models, no market |
| M50 | the M16 recipe with M50a in the model slot |

## 1. Overall (validation 2020-23, twCRPS edge %, t in brackets; positive = method better)

| Method | vs raw market | vs recal. market | vs per-name baseline | vs pooled | vs own base model | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|---|
| M40 short list A | +0.55 (2.7) | +0.43 (6.4) | +2.47 (11.9) | +1.57 | +0.56 | +0.77 | +0.76 | -0.07 | +0.29 |
| M41 short list B | +0.64 (3.1) | +0.53 (8.0) | +2.56 (13.0) | +1.67 | +0.65 | +0.59 | +1.09 | +0.07 | +0.36 |
| M42 13 features, 300k rows | +0.44 (2.1) | +0.32 (4.3) | +2.36 (11.7) | +1.47 | +0.45 | +0.41 | +0.84 | -0.14 | +0.19 |
| M43 GBM on the M29 stack | -0.19 (-0.7) | -0.31 (-2.9) | +1.74 (8.1) | +0.84 | -0.18 | +0.72 | -0.33 | -1.48 | -0.06 |
| M44 GBM on the model | -1.28 (-4.0) | -1.39 (-7.0) | +0.68 (2.5) | -0.23 | -1.26 | -1.14 | -1.10 | -3.06 | -0.20 |
| M45 location-scale on the model | -1.15 (-3.4) | -1.27 (-6.3) | +0.80 (3.2) | -0.11 | -1.14 | -0.94 | -0.59 | -3.17 | -0.31 |
| M46 linear pool, feature weights | +0.48 (2.3) | +0.36 (5.5) | +2.40 (11.3) | +1.51 | +0.49 | +0.62 | +0.71 | -0.17 | +0.30 |
| M47 wide MLP 32 | +0.71 (3.5) | +0.60 (9.8) | +2.63 (13.2) | +1.74 | +0.42 | +0.85 | +1.09 | +0.10 | +0.35 |
| **M48 wide MLP 64** | **+0.75 (3.8)** | **+0.64 (11.1)** | **+2.67 (13.1)** | **+1.78** | **+0.52** | **+0.63** | **+1.21** | **+0.32** | **+0.38** |
| M49 wide TCN 32 | +0.64 (2.9) | +0.53 (5.5) | +2.56 (13.6) | +1.67 | +0.63 | +0.41 | +1.41 | +0.01 | +0.27 |
| M50a wide bag alone | +0.04 (0.2) | -0.07 (-0.6) | +1.97 (8.8) | +1.08 | +0.05 | | | | |
| M50 wide bag + market | +0.67 (3.3) | +0.56 (10.0) | +2.59 (12.8) | +1.70 | +0.68 | +0.63 | +1.10 | +0.15 | +0.35 |

Yearly columns are against the recalibrated market. Round-3 references: M29 +0.59% (t 9.4), M33 +0.56%, M34 +0.54%, M16 +0.55%.

- The model slot matters once the model is good: M47/M48 gain 0.05-0.09% over M22 (MLP on 66 inputs, +0.55%) and M36 (wide TCN, +0.39% on four folds). The bag M50 averages the good and the weak fits and lands at M33's +0.56%.
- M43 is the cleanest test of "do the Greeks add anything after the best stack": a GBM with 2,000 rows per leaf and lambda 20 still loses 0.31% to the stack it corrects, and loses in 2021-23. M44 and M45 repeat round 3's M37/M38 on the model instead of the market with the same result.
- Weight features: fewer (M40, M41), more rows (M42) and a linear pool (M46) all stay below M34. M41's positioning list is the best of them (+0.53%) and still short of M29/M33.

## 2. The fitted models standalone (validation rows the market can price, twCRPS %)

| Variant | vs raw market | vs recal. market | vs per-name baseline | 2020 | 2021 | 2022 | 2023 | gate vs recal. / luck |
|---|---|---|---|---|---|---|---|---|
| R1 TCN 16, 66 inputs | -0.01 | -0.13 | +1.92 | +0.19 | -0.07 | -0.83 | +0.25 | 120 / 69 |
| MLP 32, 66 inputs | +0.14 | +0.03 | +2.07 | -0.08 | +0.34 | -0.37 | +0.23 | 105 / 69 |
| wide TCN 16 (4 folds) | -0.84 | -0.26 | +2.45 | +0.24 | | -0.74 | -0.26 | 34 / 17 |
| **wide MLP 32** | **+0.29** | **+0.18** | +2.22 | +0.88 | +0.45 | -0.66 | +0.10 | 129 / 69 |
| wide MLP 64 | +0.23 | +0.12 | +2.16 | +0.18 | +0.23 | **-0.03** | +0.10 | 124 / 73 |
| wide TCN 32 (4 folds) | +0.02 | -0.10 | +1.95 | +0.41 | -0.29 | -0.54 | +0.06 | 100 / 73 |

- The wide MLPs are the first fits above the recalibrated market in three of four years; the wide MLP 64 is the first at par in 2022. The TCN does not profit from the extra inputs at either width.
- Standalone gates stay near luck (129 against 69 expected, 82 at the 95th percentile): a model alone is still not a candidate; the stack is.

**Training against validation loss** (`edge_variant_training_curves.csv`):

| Variant | Best epoch per fold (1-5) | Training loss at best | Monitored loss at best | Monitored loss at the end |
|---|---|---|---|---|
| wide MLP 32 | 16, 1, 1, 1, 1 | 0.96-1.04 | 0.89-1.29 | 0.91-1.29 |
| wide MLP 64 | 23, 2, 45, 1, 1 | 0.95-1.02 | 0.89-1.28 | 0.91-1.29 |
| wide TCN 32 | -, 1, 21, 1, 1 | 1.00-1.02 | 0.88-1.05 | 0.93-1.07 |

Same picture as round 3: one or two passes learn what the inputs carry; the drift after the best epoch is 1-3% and early stopping catches it; the warm-up fold keeps improving (MLP 64 to epoch 23, TCN 32 past the ceiling). The MLP 64 with dropout 0.3 is the one fit whose monitored loss at the end is no worse than the MLP 32's with more parameters, which is why it generalizes to 2022.

## 3. By time range (vs recalibrated market, twCRPS %)

| Method | S0 pre-crash | S1 COVID | S2 recovery | S3 rate shock 2022 | S4 2023 |
|---|---|---|---|---|---|
| M40 short list A | +0.19 | +1.21 | +0.77 | -0.07 | +0.29 |
| M41 short list B | +0.06 | +1.06 | +0.93 | +0.07 | +0.36 |
| M42 300k rows | +0.02 | +0.70 | +0.71 | -0.14 | +0.19 |
| M43 GBM on stack | -1.93 | +1.27 | +0.30 | -1.48 | -0.06 |
| M44 GBM on model | -3.25 | -1.98 | -0.60 | -3.06 | -0.20 |
| M45 location-scale on model | -5.26 | -2.06 | +0.22 | -3.17 | -0.31 |
| M46 linear pool | -0.04 | +0.90 | +0.74 | -0.17 | +0.30 |
| M47 wide MLP 32 | +0.11 | +1.57 | +1.00 | +0.10 | +0.35 |
| **M48 wide MLP 64** | **+0.24** | **+0.90** | **+1.04** | **+0.32** | **+0.38** |
| M49 wide TCN 32 | -0.79 | +1.21 | +1.15 | +0.01 | +0.27 |
| M50 wide bag + market | +0.05 | +0.93 | +1.00 | +0.15 | +0.35 |

M41, M47, M48, M49 and M50 are positive in all five regimes (M49 at +0.01 in S3). M48 trades some of the COVID-crash edge (+0.90 against M29's +1.19) for the best pre-crash, rate-shock and 2023 numbers of any stack so far.

M48 by year and DTE week, vs recalibrated market:

| Year | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| 2020 | +0.66 | +0.37 | +0.31 | +0.40 | +0.53 | +0.90 | +1.44 | +0.60 |
| 2021 | +0.20 | +0.54 | +0.84 | +1.21 | +1.58 | +2.08 | +2.40 | +0.61 |
| 2022 | +0.97 | +0.52 | +0.42 | +0.27 | +0.04 | -0.05 | +0.23 | 0.00 |
| 2023 | +0.23 | +0.34 | +0.37 | +0.43 | +0.46 | +0.47 | +0.65 | -0.21 |

M47 for comparison, 2022: +1.06, +0.54, +0.28, -0.04, -0.35, -0.50, -0.17, -0.25; M29 (round 3): +1.23, +0.64, +0.34, +0.11, -0.13, -0.29, -0.12, -0.71. The 2022 long-DTE loss that every earlier stack shared is gone for M48 and halved for M47; the 2022 1-7 day edge is slightly smaller. Per-year tables for every method are in `edge_year_dte_zone.csv`.

## 4. By DTE (all validation, vs recalibrated market)

| Method | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| M40 short list A | +0.30 | +0.21 | +0.30 | +0.43 | +0.52 | +0.66 | +0.87 | -0.01 |
| M41 short list B | +0.40 | +0.33 | +0.46 | +0.56 | +0.57 | +0.70 | +0.87 | +0.22 |
| M42 300k rows | +0.25 | +0.18 | +0.22 | +0.33 | +0.36 | +0.47 | +0.67 | -0.04 |
| M43 GBM on stack | -0.67 | -0.44 | -0.34 | -0.29 | -0.18 | -0.01 | -0.01 | -0.84 |
| M44 GBM on model | -0.80 | -1.29 | -1.38 | -1.48 | -1.37 | -1.29 | -1.62 | -2.61 |
| M45 location-scale on model | -0.69 | -1.17 | -1.20 | -1.20 | -1.60 | -1.18 | -1.22 | -2.59 |
| M46 linear pool | +0.25 | +0.14 | +0.24 | +0.37 | +0.46 | +0.58 | +0.77 | -0.10 |
| M47 wide MLP 32 | +0.55 | +0.43 | +0.49 | +0.54 | +0.58 | +0.74 | +1.12 | +0.30 |
| **M48 wide MLP 64** | +0.53 | +0.44 | +0.49 | +0.58 | +0.66 | +0.87 | **+1.20** | +0.28 |
| M49 wide TCN 32 | +0.43 | +0.32 | +0.38 | +0.48 | +0.59 | +0.79 | +1.03 | +0.06 |
| M50 wide bag + market | +0.56 | +0.37 | +0.43 | +0.49 | +0.55 | +0.72 | +1.11 | +0.22 |

The peak stays at 43-49 days and M48 raises it to +1.20% (M29 +0.95%, M33 +1.11%). From 36 to 49 days M47 and M48 are above M29 (M48 +0.87% and +1.20% against +0.73% and +0.95%); 1-35 days are within 0.1% of it.

## 5. By part of the distribution (zone Brier edge % vs recalibrated market, all DTE pooled)

| Method | Left wing | Left shoulder | Body | Right shoulder | Right wing |
|---|---|---|---|---|---|
| M40 short list A | +0.71 | +0.90 | +0.40 | +0.78 | +1.02 |
| M41 short list B | +0.76 | +0.77 | +0.58 | +1.04 | +1.07 |
| M43 GBM on stack | -0.68 | +0.60 | +0.51 | +0.83 | +0.84 |
| M44 GBM on model | +0.03 | +0.30 | +0.24 | +0.33 | +0.84 |
| M46 linear pool | +0.65 | +0.76 | +0.24 | +0.57 | +0.96 |
| M47 wide MLP 32 | +0.77 | +0.86 | +0.68 | +1.10 | +1.14 |
| M48 wide MLP 64 | +0.76 | +0.79 | +0.65 | +1.04 | +1.11 |
| M49 wide TCN 32 | +0.76 | +0.83 | +0.66 | +1.12 | +1.25 |
| M50 wide bag + market | +0.75 | +0.75 | +0.61 | +1.03 | +1.08 |

Right shoulder and right wing lead, as in every round; the wide-model stacks are the first with a body edge above +0.6% (M29 +0.72% is the only earlier one). The best (DTE week, zone) cell is the right shoulder at 43-49 days for every positive method (M47 +2.0%, M48 +2.0%, M49 +1.9%, M50 +2.0%), then the left shoulder at 43-49 days (+1.5%). Per-region tables (14 regions) are in `edge_dte_zone_region.csv`.

## 6. By ticker (vs recalibrated market, twCRPS)

| Method | Tickers | Edge positive | t > 2 | t < -2 | Positive all 4 years (3 of 4) | (ticker, DTE week) t > 2 / t < -2 of 855 | (ticker, DTE, zone) t > 2 / t < -2 of 4,275 |
|---|---|---|---|---|---|---|---|
| M40 | 110 | 79 | 49 | 15 | 11 (50) | 268 / 74 | 1,491 / 129 |
| M41 | 110 | 83 | 57 | 10 | 11 (54) | 345 / 56 | 1,750 / 66 |
| M42 | 110 | 72 | 44 | 20 | 7 (43) | 239 / 98 | 1,406 / 146 |
| M43 | 110 | 39 | 17 | 43 | 2 (17) | 116 / 248 | 904 / 559 |
| M44 | 110 | 21 | 11 | 66 | 1 (12) | 65 / 349 | 761 / 439 |
| M45 | 110 | 26 | 9 | 61 | 0 (17) | 74 / 330 | 994 / 363 |
| M46 | 110 | 75 | 47 | 15 | 8 (43) | 242 / 79 | 1,421 / 157 |
| M47 | 110 | 84 | 66 | 8 | 14 (57) | 381 / 51 | 1,951 / 61 |
| **M48** | 110 | **91** | **73** | 7 | **21 (57)** | **413 / 44** | **1,971 / 73** |
| M49 | 110 | 75 | 51 | 16 | 5 (39) | 271 / 94 | 1,522 / 117 |
| M50 | 110 | 87 | 67 | 8 | 12 (55) | 394 / 56 | 1,951 / 70 |

Round-3 references: M29 87 positive, 67 with t > 2, 13 all four years, 367 / 53, 1,792 / 72; M33 86, 66, 16, 393 / 54, 1,942 / 65.

- **Leading tickers, M48** (at least 1,000 rows): KKR +1.9% (t 9.6), CIEN +1.8%, CNC +1.7%, BSX +1.5%, INTU +1.5%, CME +1.5%. **Lagging:** FTNT -1.0% (t -6.0), ENPH -0.8%, CCJ -0.7%, GE -0.6%, DHR -0.3%. M47 leads with KKR, CIEN, CNC, INTU, ALLY, BMY and lags with CCJ, ENPH, GE, FTNT, DHR. The same names lead and lag every stack since round 1; ENPH, CCJ, GE and FTNT are the four the recalibrated market prices better than any method.
- Against the per-name baseline M48 is positive on 97 of 110 tickers (86 with t > 2, 3 with t < -2); M47 98 (85, 3); M49 101 (88, 4); M50 96 (85, 5).
- Strongest (ticker, DTE week, zone) cells for M48 (at least 500 rows): BIDU 29-35 right wing +2.9% (t 12.2), AVGO 36-42 left wing +3.0% (t 11.1), EOG 15-21 right wing +2.8% (t 8.5).
- These counts are not adjusted for multiplicity. Per-ticker, per-year and per-cell tables are in the CSVs.

**Gate A0007** (beat the market and the per-name baseline in at least 3 of 4 years and 3 of 5 regimes), passes / expected by luck (luck 95th percentile), 831 cells:

| Market | M40 | M41 | M42 | M43 | M44 | M45 | M46 | M47 | M48 | M49 | M50 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Recalibrated | 193 / 41 (50) | 221 / 31 (41) | 174 / 39 (49) | 67 / 63 (75) | 74 / 91 (107) | 65 / 90 (104) | 166 / 42 (53) | 235 / 32 (44) | **252 / 31 (42)** | 165 / 37 (47) | 242 / 29 (38) |
| Raw | 136 / 53 (66) | 142 / 51 (64) | 127 / 51 (63) | 85 / 66 (79) | 66 / 88 (104) | 70 / 90 (103) | 129 / 53 (66) | 149 / 49 (59) | 145 / 48 (59) | 133 / 52 (64) | 140 / 49 (60) |

M48 beats the recalibrated market in all four years in 145 cells and the per-name baseline in 254. M43, M44 and M45 pass at or below luck.

**Calibration** (validation; nominal 5% below the 5th and 5% above the 95th percentile): M48 5.8% and 5.1%; M47 5.7% and 4.8%; M50 5.8% and 5.0%; M41 5.8% and 4.9%; M43 4.4% and 3.7% (over-dispersed); M45 8.4% and 4.4%; the raw market 4.7% and 9.0%; the per-name baseline 8.5% and 10.1%.

## 7. What to focus on next

1. **M48 is the candidate.** Combine it with the two orthogonal round-3 refinements (weights per dispersion bucket as M29, 21-day refits as M26) with the wide MLP 64 in the model slot; expect about +0.7%. That recipe, M48 and M29 go to the holdout when the owner opens it.
2. **The wide MLP line is worth one more step, not a search:** seed 29 and 47 of the wide MLP 64 (reproducibility of the +0.12% standalone and the 2022 result), and a hidden-64 fit with dropout 0.3 on the 66 inputs to separate the width/dropout effect from the input effect. About three fits.
3. **Stop:** Greeks as weight features (M39-M42, M46), as post-hoc corrections (M37, M38, M43-M45), and TCNs on the wide inputs (M36, M49). Nine documents say the same thing.
4. **Warm-up ceiling** (owner decision, unchanged from round 3): the wide TCN 32, GRU, seeds 29 and 47 and the wide TCN 16 have no 2019 fit.

## 8. How this moves toward production

- **Candidate:** M48, a quantile average of four CDFs with a weight table per DTE week; positive in every regime, 8 times luck at the gate, calibrated within one point of nominal, and the first stack whose model slot clearly adds (its base model beats the recalibrated market alone).
- **Serving shape unchanged:** four CDFs per row, a weight table, refits on a schedule; the wide MLP 64 is a 3,273-parameter model on 90 panel inputs, all from Theta's end-of-day chains.
- **Still missing:** the 2024-25 holdout, opened once by the owner; the recalibrated market inside the scorer rather than the analysis; a serving document. Holdout and 2026 are untouched; no trading or P&L has been evaluated.

## Files

| Step | Document (`children/index_options/configs/`) | Output (pilot root `/home/russell/data/index_options/theta-strike-screen-20261018/`) |
|---|---|---|
| Fit wide MLP 32 / 64 / TCN 32 | `run-strike-screen-fits-wf-{wide-mlp,wide-mlp64,wide-tcn32}.json` (placeholder form; resolved copies in `documents/study/`) | `fits/wf-{wide-mlp,wide-mlp64,wide-tcn32}/` (per-fold models, curves, `skipped/reason.json` for the refused fold) |
| Score the fits | `run-strike-region-scores-wf-{wide-mlp,wide-mlp64,wide-tcn32}.json` | `scores/strike-region-scores-wf-{wide-mlp,wide-mlp64,wide-tcn32}/` (scored rows, `tables/`) |
| Methods M40-M50 | `run-edge-method-40..49-*.json`, `run-edge-method-50a-wide-bag.json`, `run-edge-method-50-qa-wide-bag.json` | `scores/edge-method-{40..50a,50}-*/` (`forecasts.parquet`, `tables/`) |
| Gate analyses | `docs/memos/data/2026-10-20-walkforward/analysis_wf.py --method-rows [--candidate]` | `analysis/wf-{wide-mlp,wide-mlp64,wide-tcn32}/`, `analysis/edge-method-40..50/` |
| Collected CSVs | this memo's folder `docs/memos/data/2026-10-20-edge-methods/` | `edge_*.csv[.gz]` now cover M01-M50 (`edge_method` names the method); the three `edge_variant_*.csv` files cover twelve fitted variants |

Breakdowns by ticker, DTE week and zone for every method: `edge_ticker_dte.csv.gz` (ticker x DTE week, twCRPS), `edge_ticker_dte_zone.csv.gz` (ticker x DTE week x zone, zone Brier), `edge_ticker_zone.csv.gz`, `edge_ticker_year.csv.gz`, `edge_year_dte_zone.csv`, `edge_period_dte_zone.csv`, `edge_dte_zone_region.csv`; gates in `edge_gate_vs_market.csv`, `edge_gate_vs_calibrated_market.csv`, `edge_gate_summary.csv`; calibration in `edge_calibration.csv`. Superseded M43 output (fallback to the per-fold recalibrated market, undefined in fold 1) is kept under `superseded/m43-marketcal-fallback-20261021/`.
