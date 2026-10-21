# ADR-0270 edge methods round 3: architectures, training diagnosis, Theta Greeks, 19 more methods (2026-10-20)

## TL;DR

- **The best recipe is unchanged in kind and slightly better in detail.** A quantile average of model + recalibrated market + pooled empirical + per-name baseline, with weights learned on earlier years, now scores **+0.59% twCRPS over the recalibrated market (t 9.4)** when the weights are set per market-dispersion bucket (M29), **+0.58% (t 8.7)** when refitted every 21 days per DTE week (M26), and **+0.56% (t 10.0)** when the model slot holds a bag of eight fitted variants (M33). Round 2's M16 was +0.55%.
- **Every one of M22, M23, M24, M26, M29, M33 and M36 is positive in all five regimes**, including the 2022 rate shock (+0.09% to +0.31%). The gate passes 212-241 of 831 cells against about 28 by luck.
- **Architectures:** standalone, no fitted model beats the recalibrated market. The MLP is the only one at par (+0.03%); the TCN R1 is -0.13%, five mixture components -0.12%, the decision-Brier loss -0.15%, the GRU -0.32% (three folds), seed 47 -0.26%, and the 90-input "wide" model -0.26% (four folds). Inside the quantile average they are interchangeable (+0.55% to +0.56%), so the base model's job is a small, stable correction, not the forecast.
- **Training diagnosis:** the monitored loss is at its best after one or two epochs in 22 of 36 fold fits and drifts up afterwards while the training loss keeps falling 2-4%. The regularized R1 (3,001 parameters, weight decay 0.3, dropout 0.2) closed the first run's gap (TCN: training 0.79-0.91 against monitored 0.97-1.40). The remaining pattern is a model that learns what it can in one pass. More epochs help only the warm-up fold (MLP best epoch 96, wide 81).
- **Theta's Greeks, volume and open interest:** the R1 arm reads 66 inputs built from Theta's quotes, IV and open interest; it omitted 24 chain inputs (own IV, OI and its change, quote depth, volume, put/call volume imbalance, delta/gamma/vega-weighted OI, IV minus realized variance, term slopes, strike-node IVs). Added as model inputs (M36) they do not improve the standalone model; as conditioning features on the recalibrated market they lose (GBM M37 -1.26%, location-scale M38 -0.99%) and as weight features (M39, +0.35%) they are worse than the three market-shape features of M34 (+0.54%). They stay available; nothing in this round shows they add predictive information beyond the risk-neutral quantiles and the chain summary already used.
- Everything here is a predictive score difference on the 2020-23 validation years; nothing is about trading.

## What ran

Six new fits on the GPU (each 20-28 min, 8.8-9.6 GB RSS), 21 GridCDFMethod documents and 20 gate analyses. Packet 3 closed at 13,488 of 14,400 s; packet 4 (`registration/wall-used-p4.txt`) holds the wide fit and its scoring, 2,708 s. Evidence under `scores/` is 22 GB of the 24 GB cap.

| Fit | Change from R1 | Folds fitted |
|---|---|---|
| gru | one-layer GRU, hidden 16 (1,753 parameters) | 2, 4, 5 (folds 1 and 3 refused: monitor still improving at 150 epochs) |
| mlp | MLP, hidden 32 (1,833) | all 5 |
| mix5 | five mixture components instead of three (3,055) | all 5 |
| l3 | third loss term `decision_brier`, weight 1 (3,001) | all 5 |
| s47 | seed 47 | 2-5 (fold 1 refused) |
| wide | 90 inputs: R1's 66 plus the 24 omitted chain inputs (3,193) | 1, 2, 4, 5 (fold 3 refused) |

Refused folds are skips, not failures: the fit queue stops a fold whose patience was not exhausted at the 150-epoch ceiling. Methods built on those fits fall back to the R1 model for the missing fold; their cell counts are 814 instead of 831.

| # | Method (all quantile averages unless stated; four components = model, recalibrated market, pooled, per-name baseline) |
|---|---|
| M21-M24 | the M16 recipe (four components, weights per DTE week) with the GRU, MLP, mix5, L3 model in the model slot |
| M25 | bag of seeds 11, 29, 47, weights per DTE week, no market |
| M26 | M16 refitted every 21 days on settled rows |
| M27 | M16 with separate weights for the tails (levels below 0.15 and above 0.85) and the centre |
| M28 | M16 with weights per ticker |
| M29 | M16 with weights per market-dispersion bucket |
| M30 | 21-knot PIT recalibration of M26, refitted every 21 days |
| M31 | M16 with a rolling-recalibrated market (M31a) in place of the per-fold recalibration |
| M31a | the market alone, PIT-recalibrated every 21 days with a 180-day half-life |
| M32 | six components: seeds 11, 29, 47 plus the three comparators, per DTE week |
| M33a | bag of eight fitted variants (seeds 11, 29, 47, GRU, MLP, mix5, L3, wide), per DTE week, no market |
| M33 | M16 with M33a in the model slot |
| M34 | four components, weights a softmax of log DTE, market standard deviation and the market 95-5 spread |
| M35 | location-scale adjustment of the recalibrated market from those three features, per DTE week |
| M36 | M16 recipe on the wide model |
| M37 | monotone LightGBM on the recalibrated market with log DTE and ten Greeks/volume/OI inputs |
| M38 | M35 with the ten Greeks/volume/OI inputs added |
| M39 | M34 with the ten Greeks/volume/OI inputs added |

## 1. Overall (validation 2020-23, twCRPS edge %, t in brackets; positive = method better)

| Method | vs raw market | vs recal. market | vs per-name baseline | vs pooled | vs base model |
|---|---|---|---|---|---|
| M21 GRU model (3 folds) | -0.39 (-1.5) | +0.18 (2.1) | +2.89 (13.1) | +1.43 | +0.50 |
| M22 MLP model | +0.66 (3.3) | +0.55 (10.0) | +2.58 (12.8) | +1.69 | +0.52 |
| M23 mix5 model | +0.67 (3.3) | +0.56 (9.7) | +2.59 (12.8) | +1.70 | +0.68 |
| M24 L3 model | +0.66 (3.2) | +0.55 (9.5) | +2.58 (12.8) | +1.69 | +0.69 |
| M25 seed bag | +0.27 (1.2) | +0.15 (1.5) | +2.19 (10.2) | +1.30 | +0.28 |
| M26 rolling per DTE | +0.69 (3.3) | +0.58 (8.7) | +2.61 (13.3) | +1.72 | +0.70 |
| M27 level bands | +0.61 (2.8) | +0.49 (8.2) | +2.53 (12.7) | +1.63 | +0.62 |
| M28 per ticker | +0.46 (2.2) | +0.35 (4.3) | +2.39 (11.4) | +1.49 | +0.48 |
| **M29 per dispersion** | **+0.70 (3.5)** | **+0.59 (9.4)** | **+2.62 (12.8)** | **+1.73** | **+0.72** |
| M30 rolling PIT of M26 | -0.10 (-0.4) | -0.21 (-1.9) | +1.84 (9.5) | +0.94 | -0.09 |
| M31 rolling market inside | +0.44 (2.0) | +0.33 (3.2) | +2.37 (12.5) | +1.47 | +0.45 |
| M31a rolling market alone | -0.75 (-3.2) | -0.86 (-4.3) | +1.20 (4.9) | +0.30 | -0.74 |
| M32 six components | +0.56 (2.6) | +0.44 (5.1) | +2.48 (13.3) | +1.59 | +0.57 |
| M33a model bag alone | +0.10 (0.4) | -0.02 (-0.2) | +2.03 (9.0) | +1.13 | +0.11 |
| **M33 model bag + market** | **+0.67 (3.3)** | **+0.56 (10.0)** | **+2.59 (12.8)** | **+1.70** | **+0.68** |
| M34 feature weights | +0.65 (3.1) | +0.54 (8.0) | +2.57 (12.4) | +1.68 | +0.66 |
| M35 location-scale | -0.28 (-1.1) | -0.39 (-4.6) | +1.66 (6.7) | +0.76 | -0.27 |
| M36 wide model (4 folds) | -0.18 (-0.7) | +0.39 (8.3) | +3.09 (13.5) | +1.63 | +0.65 |
| M37 GBM Greeks | -1.14 (-4.4) | -1.26 (-8.4) | +0.81 (2.9) | -0.09 | -1.13 |
| M38 location-scale Greeks | -0.88 (-3.2) | -0.99 (-8.8) | +1.07 (4.3) | +0.17 | -0.87 |
| M39 Greeks weights | +0.47 (2.3) | +0.35 (4.9) | +2.39 (11.9) | +1.50 | +0.48 |

- M21 and M36 miss 2021, the strongest year (+1.0% to +1.1% for the others); on the three years they have, M36 is +0.39% against M16's +0.55% and M33's +0.56%. The wide inputs do not help.
- M31 is +1.18% (t 8.2) over its own rolling-recalibrated market, but that market (M31a) is worse than the per-fold recalibration by 0.86%: 21-day refits on a 180-day half-life chase the crash (S1 -6.7%).
- Recalibrating a stack again (M30) and shifting or scaling the market from features (M35, M37, M38) lose; the same lesson as M07 and M10 in round 1.
- M25 and M33a: bags of models beat any single model (+0.15% and -0.02% against the recalibrated market, where the singles are -0.13% to -0.32%) but are not a substitute for mixing with the market.

## 2. Architecture and training diagnosis (the fitted models standalone, validation rows the market can price)

| Variant | vs raw market | vs recal. market | vs per-name baseline | 2020 | 2021 | 2022 | 2023 | gate vs recal. / luck |
|---|---|---|---|---|---|---|---|---|
| TCN first run (12k params) | -0.67 | -0.78 | +1.28 | -0.72 | +0.13 | -1.74 | -0.79 | 93 / 80 |
| R1 TCN (3,001) | -0.01 | -0.13 | +1.92 | +0.19 | -0.07 | -0.83 | +0.25 | 120 / 69 |
| GRU (3 folds) | -0.90 | -0.32 | +2.40 | +0.14 | | -1.26 | +0.21 | 25 / 16 |
| **MLP** | **+0.14** | **+0.03** | +2.07 | -0.08 | +0.34 | -0.37 | +0.23 | 105 / 69 |
| mix5 | -0.01 | -0.12 | +1.93 | -0.04 | -0.17 | -0.26 | -0.02 | 118 / 71 |
| L3 | -0.04 | -0.15 | +1.90 | +0.19 | -0.11 | -0.90 | +0.26 | 121 / 69 |
| seed 47 (4 folds) | -0.14 | -0.26 | +1.79 | -0.25 | -0.33 | -0.58 | +0.16 | 115 / 76 |
| wide (4 folds) | -0.84 | -0.26 | +2.45 | +0.24 | | -0.74 | -0.26 | 34 / 17 |

Yearly columns are against the recalibrated market. Every variant beats the per-name baseline by 1.8-2.5% and loses to the market in 2022.

**Training against validation loss** (per-epoch curves in `edge_variant_training_curves.csv`):

| Variant | Best epoch per fold (1-5) | Training loss at best | Monitored loss at best | Monitored loss at the end |
|---|---|---|---|---|
| TCN first run | 1, 2, 1, 2, 2 | 0.99-1.05 (0.79-0.91 at the end) | 0.90-1.31 | 0.97-1.40 |
| R1 | 23, 1, 20, 1, 2 | 0.96-1.02 | 0.88-1.26 | 0.89-1.27 |
| GRU | -, 2, -, 13, 1 | 0.99-1.01 | 0.88-1.05 | 0.90-1.05 |
| MLP | 96, 1, 1, 1, 1 | 0.96-1.04 | 0.88-1.26 | 0.91-1.29 |
| mix5 | 23, 1, 48, 1, 44 | 0.96-1.02 | 0.89-1.26 | 0.89-1.28 |
| L3 | 23, 1, 48, 1, 2 | 1.07-1.14 | 0.99-1.39 | 1.00-1.42 |
| seed 47 | -, 43, 18, 1, 6 | 0.99-1.01 | 0.88-1.05 | 0.89-1.06 |
| wide | 81, 1, -, 1, 14 | 0.95-1.01 | 0.89-1.26 | 0.90-1.29 |

- **Overfitting:** present and mild. In the first run the training loss fell 20% below the monitored loss; with R1's regularization the training loss (measured with dropout on) sits at or above the monitored loss, and the drift after the best epoch is 0.5-3% of the monitored loss, caught by early stopping. The L3 numbers are higher because its loss has a third term.
- **Underfitting:** the best epoch is 1 or 2 in most folds. One pass over 260k-970k rows at learning rate 3e-4 and batch 512 is enough for what the model learns from these inputs; later epochs do not find more. The warm-up fold (2017-18 fitting, 2019 monitoring) is the exception: it keeps improving to epoch 81-96 for the MLP and wide models and past the ceiling for GRU, seed 29 and seed 47. A longer ceiling for that fold is an owner decision (packet time).
- **Optimizer and regularization:** unchanged from R1 (AdamW, weight decay 0.3, dropout 0.2, learning rate 3e-4, patience 50). Round 2's learning-rate 1e-4 variant was no better. Nothing in the curves points at the optimizer; the ceiling is the information in the inputs.
- **Architectures:** the non-complex MLP equals the TCN and beats it standalone; the GRU is worse and slower to converge; more mixture components and the extra loss change nothing. Complexity is not the lever.

## 3. By time range (vs recalibrated market, twCRPS %)

| Method | S0 pre-crash | S1 COVID | S2 recovery | S3 rate shock 2022 | S4 2023 |
|---|---|---|---|---|---|
| M22 MLP | +0.16 | +0.83 | +0.92 | +0.22 | +0.35 |
| M23 mix5 | +0.17 | +0.80 | +0.95 | +0.25 | +0.32 |
| M24 L3 | +0.04 | +0.95 | +1.00 | +0.09 | +0.35 |
| M26 rolling | +0.05 | +0.74 | +1.08 | +0.17 | +0.33 |
| M27 level bands | -0.29 | +0.85 | +1.06 | -0.10 | +0.35 |
| M28 per ticker | +0.13 | +0.85 | +0.92 | -0.44 | +0.23 |
| **M29 per dispersion** | **+0.14** | **+1.19** | **+0.98** | **+0.21** | **+0.35** |
| M32 six | -1.05 | +0.70 | +1.04 | -0.01 | +0.32 |
| **M33 model bag + market** | **+0.05** | **+0.93** | **+1.00** | **+0.16** | **+0.34** |
| M34 feature weights | +0.08 | +1.52 | +0.94 | -0.00 | +0.33 |
| M36 wide (no 2021) | +0.10 | +0.85 | +0.71 | +0.31 | +0.27 |
| M39 Greeks weights | +0.02 | +0.70 | +0.71 | -0.11 | +0.28 |
| M35 location-scale | -0.68 | +1.67 | -0.09 | -1.97 | +0.27 |
| M37 GBM Greeks | -0.76 | -0.15 | -1.58 | -2.36 | -0.06 |

M29 by year and DTE week, vs recalibrated market:

| Year | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| 2020 | +0.82 | +0.52 | +0.62 | +0.66 | +0.78 | +1.05 | +1.21 | +0.86 |
| 2021 | +0.15 | +0.41 | +0.67 | +1.05 | +1.29 | +1.73 | +2.02 | +0.76 |
| 2022 | +1.23 | +0.64 | +0.34 | +0.11 | -0.13 | -0.29 | -0.12 | -0.71 |
| 2023 | +0.22 | +0.27 | +0.38 | +0.40 | +0.39 | +0.38 | +0.62 | -0.17 |

The 2022 shape repeats from round 2: the edge holds for 1-4 weeks and is lost beyond. M36 (wide inputs) is the method with the smallest 2022 loss beyond 4 weeks (+0.03, -0.06, +0.14, +0.09 against -0.13 to -0.71 for M29), the one place the extra chain inputs show.

## 4. By DTE (all validation, vs recalibrated market)

| Method | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| M22 MLP | +0.57 | +0.37 | +0.43 | +0.49 | +0.53 | +0.69 | +1.05 | +0.23 |
| M26 rolling | +0.62 | +0.32 | +0.39 | +0.51 | +0.59 | +0.80 | +1.09 | +0.27 |
| M29 per dispersion | +0.61 | +0.46 | +0.50 | +0.56 | +0.59 | +0.73 | +0.95 | +0.21 |
| M33 model bag + market | +0.54 | +0.35 | +0.41 | +0.50 | +0.55 | +0.75 | +1.11 | +0.25 |
| M34 feature weights | +0.33 | +0.33 | +0.42 | +0.49 | +0.57 | +0.79 | +1.08 | +0.27 |
| M36 wide | +0.75 | +0.35 | +0.29 | +0.28 | +0.27 | +0.35 | +0.67 | +0.22 |
| M39 Greeks weights | +0.29 | +0.21 | +0.24 | +0.35 | +0.39 | +0.52 | +0.70 | -0.03 |
| M37 GBM Greeks | -0.21 | -0.68 | -0.88 | -1.23 | -1.42 | -1.97 | -2.09 | -2.23 |

The peak is 43-49 days for every positive method; M29 is the flattest across DTE (its weights follow dispersion, not DTE). The Greeks-conditioned market adjustments lose more the longer the expiry.

## 5. By part of the distribution (zone Brier edge % vs recalibrated market, all DTE pooled)

| Method | Left wing | Left shoulder | Body | Right shoulder | Right wing |
|---|---|---|---|---|---|
| M22 MLP | +0.73 | +0.74 | +0.62 | +1.03 | +1.08 |
| M26 rolling | +0.80 | +0.78 | +0.64 | +1.06 | +1.11 |
| M29 per dispersion | +0.72 | +0.86 | +0.72 | +1.06 | +1.09 |
| M33 model bag + market | +0.75 | +0.75 | +0.62 | +1.03 | +1.08 |
| M36 wide | +0.48 | +0.59 | +0.49 | +0.91 | +1.00 |
| M39 Greeks weights | +0.68 | +0.83 | +0.29 | +0.69 | +1.00 |
| M38 location-scale Greeks | +0.09 | -0.02 | -0.26 | -0.57 | +0.25 |

Right shoulder and right wing lead everywhere, as in rounds 1-2. The best (DTE week, zone) cell for every positive method is the right shoulder at 43-49 days (+1.8% to +2.0%), then the right wing at 36-42 days (+1.4% to +1.6%). Per-region (14 regions) tables are in `edge_dte_zone_region.csv`.

## 6. By ticker (vs recalibrated market, twCRPS)

| Method | Tickers | Edge positive | t > 2 | t < -2 | Positive all 4 years | (ticker, DTE week) t > 2 / t < -2 of 855 | (ticker, DTE, zone) t > 2 / t < -2 of 4,275 |
|---|---|---|---|---|---|---|---|
| M29 | 110 | 87 | 67 | 7 | 13 (60 in at least 3) | 367 / 53 | 1,792 / 72 |
| M33 | 110 | 86 | 66 | 8 | 16 (57 in at least 3) | 393 / 54 | 1,942 / 65 |
| M22 | 110 | 84 | 69 | 8 | 15 (59 in at least 3) | | |
| M36 | 108 | 88 | 61 | 9 | | 329 / 60 of 837 | |
| M39 | 110 | 72 | 45 | 15 | | | |
| M37 | 110 | 24 | 8 | 70 | | | |

- **Leading tickers, M29** (at least 1,000 rows): KKR +2.8% (t 9.6), CIEN +2.2%, CNC +2.1%, CME +1.7%, INTU +1.6%, APO +1.5%. **Lagging:** ENPH -1.2% (t -3.7), GE -0.8%, CPB -0.6%, CCJ -0.6%, FTNT -0.5%. M33 leads with KKR, CIEN, CNC, CME, BSX, KR and lags with ENPH, FTNT, CCJ, GE.
- Against the per-name baseline M29 is positive on 96 of 110 tickers (85 with t > 2, 4 with t < -2); M33 96 (84, 5); M36 96 of 108.
- These counts are not adjusted for multiplicity. Per-ticker, per-year and per-cell tables are in the CSVs.

**Gate A0007** (beat the market and the per-name baseline in at least 3 of 4 years and 3 of 5 regimes), passes / expected by luck (luck 95th percentile):

| Market | M22 | M23 | M24 | M26 | M27 | M29 | M33 | M34 | M36 (814) | M39 |
|---|---|---|---|---|---|---|---|---|---|---|
| Recalibrated | 231 / 28 (37) | 238 / 28 (37) | 234 / 28 (38) | 212 / 27 (36) | 224 / 28 (37) | 228 / 33 (42) | **241 / 28 (38)** | 214 / 38 (48) | 79 / 3 (6) | 172 / 38 (48) |
| Raw | 140 / 49 (60) | 137 / 49 (60) | 140 / 50 (62) | 132 / 50 (62) | 135 / 51 (63) | 141 / 50 (63) | 138 / 50 (60) | 140 / 54 (65) | 15 / 12 (17) | 135 / 51 (62) |

The standalone models pass at the luck rate (R1 120 / 69, MLP 105 / 69). M30, M31a, M35, M37 and M38 pass at or below luck.

**Calibration** (validation; nominal 5% below the 5th and 5% above the 95th percentile): M29 5.7% and 4.9%; M33 5.8% and 5.0%; M22 5.5% and 5.2%; M36 6.2% and 4.7%; the raw market 4.7% and 9.0%; the per-name baseline 8.5% and 10.1%.

## 7. What to focus on next

1. **One recipe, two refinements:** M29's dispersion buckets and M26's 21-day refits are the two gains over M16 and they are orthogonal. Combine them (quantile average, weights per dispersion bucket, refit every 21 days) and put the eight-variant bag or the MLP in the model slot. Expect +0.6%; that is the candidate for the holdout.
2. **Stop spending on architectures and inputs.** Six architectures, three seeds, two losses and 24 more chain inputs moved the standalone model by less than 0.2% and the stack by less than 0.05%. The remaining 2022 long-DTE loss is a weighting problem (round 2, item 2), not a model problem.
3. **Warm-up ceiling** (owner decision): raise the fold-1 ceiling so GRU, seed 29, seed 47 and wide have 2019 rows; it costs about one packet.
4. **Leave out:** rolling market recalibration on short half-lives (M31a), re-recalibrating a stack (M30), feature-driven shifts of the market (M35, M37, M38), per-ticker weights (M28).

## 8. How this moves toward production

- **Candidate:** the M16 family, best as M29 or M33, next as the combined recipe above. Positive in every regime, gate 7-8 times luck, calibrated within one point of nominal, reproducible across seeds and architectures (the model slot can hold any of six fits with the same result).
- **Serving shape is simple and now fully configured in the node:** four CDFs per row, a weight table keyed by dispersion bucket or DTE week, refits on a schedule; `feature_source` lets any kind read panel columns at serve time.
- **Theta features:** all of Theta's end-of-day Greeks, IV, quotes, volume and open interest are in the panel and reachable by every method; the evidence says the 66-input arm already carries what matters for this score.
- **Still missing:** the 2024-25 holdout, opened once by the owner for the leading recipe; the recalibrated market inside the scorer rather than the analysis; a serving document. Holdout and 2026 are untouched; no trading or P&L has been evaluated.

## Files

The data folder `data/2026-10-20-edge-methods/` now covers all 39 methods plus M31a and M33a (`edge_method` names the method; the round-2 memo lists the files). New this round: `edge_variant_perf_by_window.csv` (each fitted variant standalone, by fold, year, regime and pooled, against every comparator), `edge_variant_gate_summary.csv`, `edge_variant_training_curves.csv` (per-epoch training and monitored loss, best epoch, parameters, fit seconds).

Documents are `configs/run-edge-method-21..39-*.json` plus `31a` and `33a`. The five fit documents and their scoring documents are under the pilot root `documents/study/run-strike-screen-fits-wf-{gru,mlp,mix5,l3,s47,wide}.json`; fits under `fits/wf-*`, scored rows under `scores/strike-region-scores-wf-*`, method outputs under `scores/edge-method-*`, gate analyses under `analysis/`.
