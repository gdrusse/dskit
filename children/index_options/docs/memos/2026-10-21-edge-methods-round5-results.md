# ADR-0270 edge methods round 5: patterns, the combined recipe on the wide MLP 64, seeds (2026-10-21)

## TL;DR

- **The three levers add.** On the wide MLP 64 rows, weights per market-dispersion bucket (M51) give **+0.68% twCRPS over the recalibrated market (t 10.7)**, 21-day refits (M52) +0.66%, both together (M53) **+0.69% (t 9.8)**; round 4's M48 was +0.64%, round 3's M29 +0.59%. Buckets on the market's 90% width (M58) tie M51 at +0.68%. Nine of the twelve new stacks are positive in all five regimes; M51/M53/M58 hold +0.39% to +0.42% in the 2022 rate shock and +1.0% to +1.1% in the recovery.
- **Gate:** M54 (one-year half-life) 237, M58 235, M51 233, M52 233, M56 232 of 831 cells against 30-36 by luck; M51 and M58 beat the recalibrated market in all four years in 142-144 cells and are positive on 91 of 110 tickers; M51 has 21 tickers positive in all four years and only 36 (ticker, DTE week) cells with t < -2 of 855 (M48: 44).
- **Seeds and completion:** seed 29 alone is +0.20% over the recalibrated market, seed 11 +0.12%, seed 47 -0.02%; their bag M57a is +0.29% alone. After disk was freed on 2026-10-22, M57, the 66-input and 106-input fits, and M64/M65 completed. Their blends scored +0.58%, +0.55%, and +0.58% respectively, below M51/M53. The added inputs did not beat the 90-input model on the comparable M16 recipe.
- **What does not help:** quantile bands mirroring the zones (M55 +0.58%, negative pre-crash and in 2022), a linear pool per strike threshold (M61 +0.56%), risk-neutral shape moments as weight features (M63 +0.60%, -0.12% in 2022), the seed bag without the market, and bucketing by strike density or vol level (M59, M60 +0.58-0.59%): the model slot and the dispersion/refit levers carry the gain; new conditioning does not.
- Everything here is a predictive score difference on the 2020-23 validation years; nothing is about trading.

## Patterns across rounds 1-5 (the analysis that drove the design)

- Only the quantile-average family (model + recalibrated market + pooled + per-name baseline) clears +0.5%; linear and beta pools, selection, re-recalibration and GBM or location-scale corrections sit between +0.5% and -1.5%.
- The stack's rank follows the base model's standalone rank: R1 -0.13% alone gives +0.55% in the stack, the wide MLP 64 +0.12% gives +0.64%, seed 29 +0.20% (its stack is the next run). Bags that mix weak and good fits land at the family mean.
- Three orthogonal levers existed and had never been combined: dispersion buckets (+0.04%), 21-day refits (+0.03%), a better base model (+0.09%). Round 5 confirms they add (+0.69%).
- The 90 wide inputs (Greeks, volume, open interest, depth, own IV, strike nodes) help an MLP and not a TCN; as conditioning features in the stack they are exhausted (rounds 3-5).
- The edge sits at 29-49 days, in the right shoulder and right wing, in the COVID crash and recovery; the wide MLP removed the 2022 long-DTE loss. Ticker leaders and laggards are the same in every stack (ENPH, CCJ, GE, FTNT, CPB always behind), which asks for a covariate in the weight law rather than per-ticker cells (needs an ADR: shrunk per-ticker term).
- Unused panel columns remain: twelve risk-neutral shape moments (tried as weight features in M63, no gain), short skew and curvature changes, lagged open interest, strike nodes 0-2 and 6-8 (the 106-input wider fit ran on 2026-10-22; its one-seed result is reported below).

## What ran

At the 2026-10-21 wrap, two wide MLP 64 seed fits, thirteen GridCDFMethod documents, and fourteen gate analyses had run; M57 and M64/M65 were stopped at the C: floor. The 2026-10-22 completion ran those three documents and both remaining fits plus their scorers and analyses, all rc 0; see the addendum below. Packet 5 now totals 9,554 of 14,400 s. Evidence under scores/ is 37,968,358,276 bytes under the 40,000,000,000-byte cap.

| # | Method (all on the wide MLP 64 scored rows; quantile average of model + recalibrated market + pooled + per-name baseline unless stated) |
|---|---|
| M51 | weights per market-dispersion bucket (M29's lever) |
| M52 | weights per DTE week refitted every 21 days on settled rows (M26's lever) |
| M53 | both: per dispersion bucket, refitted every 21 days (the combined recipe) |
| M54 | weights per DTE week with a one-year half-life on the learning rows |
| M55 | five quantile-level bands mirroring the zones (0.1 / 0.3 / 0.7 / 0.9) with their own weights |
| M56 | five components: wide MLP 32 and wide MLP 64 both in the slot |
| M57a | bag of the wide MLP 64 seeds 11, 29, 47, no market (ingredient) |
| M57 | the M16 recipe with M57a in the model slot (completed 2026-10-22) |
| M58, M59, M60 | weights per bucket of the market 90% width, the listed-strike count, the reference scale (vol level) |
| M61 | linear pool per strike threshold |
| M62 | M34's market-shape weight features (log DTE, market sd, 95-5 spread) |
| M63 | weight features from the unused risk-neutral shape moments |
| M64, M65 | the M16 recipe on the 66-input MLP 64 and the 106-input wider MLP 64 (completed 2026-10-22) |

## 1. Overall (validation 2020-23, twCRPS edge %, t in brackets; positive = method better)

| Method | vs raw market | vs recal. market | vs per-name baseline | vs pooled | vs base model | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|---|
| **M51 per dispersion** | **+0.80 (4.1)** | **+0.68 (10.7)** | **+2.72 (13.2)** | +1.82 | +0.57 | +0.84 | +1.12 | +0.40 | +0.38 |
| M52 rolling 21 d | +0.77 (3.8) | +0.66 (9.6) | +2.69 (13.7) | +1.80 | +0.54 | +0.71 | +1.23 | +0.35 | +0.33 |
| **M53 rolling per dispersion** | **+0.80 (4.0)** | **+0.69 (9.8)** | **+2.72 (13.6)** | +1.83 | +0.57 | +0.93 | +1.10 | +0.42 | +0.31 |
| M54 half-life | +0.73 (3.6) | +0.62 (9.7) | +2.65 (13.2) | +1.76 | +0.50 | +0.62 | +1.34 | +0.14 | +0.37 |
| M55 zone bands | +0.70 (3.3) | +0.58 (9.0) | +2.62 (13.0) | +1.72 | +0.46 | +0.79 | +1.21 | -0.09 | +0.43 |
| M56 two MLPs | +0.73 (3.7) | +0.62 (10.1) | +2.65 (13.2) | +1.76 | +0.50 | +0.85 | +1.10 | +0.16 | +0.38 |
| M57a seed bag alone | +0.40 (2.0) | +0.29 (2.8) | +2.33 (11.2) | +1.43 | +0.17 | +0.43 | +0.82 | -0.27 | +0.20 |
| **M58 per market width** | **+0.80 (4.1)** | **+0.68 (10.7)** | **+2.72 (13.2)** | +1.82 | +0.56 | +0.81 | +1.14 | +0.39 | +0.38 |
| M59 per strike density | +0.69 (3.5) | +0.58 (10.0) | +2.61 (12.7) | +1.72 | +0.46 | +0.52 | +1.17 | +0.27 | +0.36 |
| M60 per vol level | +0.70 (3.5) | +0.59 (10.1) | +2.62 (12.6) | +1.73 | +0.47 | +0.82 | +0.99 | +0.22 | +0.31 |
| M61 linear pool per threshold | +0.68 (3.1) | +0.56 (8.3) | +2.60 (12.5) | +1.71 | +0.44 | +0.95 | +1.01 | -0.13 | +0.44 |
| M62 market-shape weights | +0.76 (3.8) | +0.65 (9.7) | +2.68 (12.8) | +1.79 | +0.53 | +0.92 | +1.07 | +0.24 | +0.37 |
| M63 rn-shape weights | +0.72 (3.3) | +0.60 (7.7) | +2.64 (12.8) | +1.75 | +0.49 | +1.16 | +1.10 | -0.12 | +0.30 |

Yearly columns are against the recalibrated market. References: M48 +0.64% (t 11.1; 2022 +0.32%), M29 +0.59%, M26 +0.58%.

- Dispersion buckets and market-width buckets are the same lever (the 90% width is a dispersion measure): identical numbers to two decimals. Strike density and vol level bucket something else and give back the M48 level.
- Refits every 21 days (M52) take the 2021 edge up (+1.23%) and the crash edge down (S1 +0.77%); the combined M53 keeps M51's crash edge and adds M52's recovery edge, at the cost of a weaker gate (below).
- Feature-weighted laws (M62, M63) reach M48's level and no more; M63 is the best COVID-crash number of any method (+1.89%) and negative in 2022.

## 2. The fitted models standalone (validation rows the market can price, twCRPS %)

| Variant | vs raw market | vs recal. market | vs per-name baseline | 2020 | 2021 | 2022 | 2023 | gate vs recal. / luck |
|---|---|---|---|---|---|---|---|---|
| wide MLP 64, seed 11 (round 4) | +0.23 | +0.12 | +2.16 | +0.18 | +0.23 | -0.03 | +0.10 | 124 / 73 |
| **wide MLP 64, seed 29** | **+0.32** | **+0.20** | +2.24 | +0.41 | +0.72 | -0.50 | +0.20 | 127 / 63 |
| wide MLP 64, seed 47 | +0.09 | -0.02 | +2.02 | -0.48 | +0.74 | -0.57 | +0.19 | 92 / 64 |
| M57a bag of the three | +0.40 | +0.29 | +2.33 | +0.43 | +0.82 | -0.27 | +0.20 | |

- Seed spread of the same architecture is 0.22% standalone (2020 ranges from -0.48% to +0.41%), which is larger than any architecture or input effect measured in rounds 3-4 except the wide-MLP step itself. Seed 11's 2022 result (-0.03%) is the outlier among the three; the bag is the honest number (-0.27% in 2022, +0.29% pooled).
- Training curves (`edge_variant_training_curves.csv`): seed 29 is at its best after one epoch in every fold; seed 47 needs 53 epochs in the warm-up fold and one epoch afterwards. Same picture as rounds 3-4: the inputs set the ceiling, one pass reaches it.

## 3. By time range (vs recalibrated market, twCRPS %)

| Method | S0 pre-crash | S1 COVID | S2 recovery | S3 rate shock 2022 | S4 2023 |
|---|---|---|---|---|---|
| **M51 per dispersion** | **+0.32** | **+1.25** | +1.04 | **+0.40** | +0.38 |
| M52 rolling | +0.24 | +0.77 | +1.14 | +0.35 | +0.33 |
| **M53 rolling per dispersion** | +0.31 | +1.23 | +1.09 | **+0.42** | +0.31 |
| M54 half-life | +0.22 | +0.88 | +1.14 | +0.14 | +0.37 |
| M55 zone bands | -0.13 | +1.04 | +1.18 | -0.09 | +0.43 |
| M56 two MLPs | +0.11 | +1.57 | +1.00 | +0.16 | +0.38 |
| M57a seed bag alone | -0.64 | +1.40 | +0.68 | -0.27 | +0.20 |
| M58 per market width | +0.31 | +1.21 | +1.05 | +0.39 | +0.38 |
| M59 per strike density | +0.41 | +0.75 | +0.94 | +0.27 | +0.36 |
| M60 per vol level | +0.35 | +1.19 | +0.95 | +0.22 | +0.31 |
| M61 linear pool | -0.19 | +1.23 | +1.12 | -0.13 | +0.44 |
| M62 market-shape weights | +0.28 | +1.48 | +1.02 | +0.24 | +0.37 |
| M63 rn-shape weights | +0.17 | +1.89 | +1.13 | -0.12 | +0.30 |

M53 by year and DTE week, vs recalibrated market:

| Year | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| 2020 | +0.74 | +0.50 | +0.65 | +0.78 | +0.97 | +1.34 | +1.61 | +0.85 |
| 2021 | +0.08 | +0.43 | +0.75 | +1.16 | +1.41 | +1.96 | +2.18 | +0.78 |
| 2022 | +1.28 | +0.78 | +0.50 | +0.32 | +0.10 | +0.01 | +0.21 | -0.28 |
| 2023 | +0.14 | +0.27 | +0.34 | +0.36 | +0.38 | +0.36 | +0.57 | -0.30 |

M51's 2022 row: +1.26, +0.77, +0.50, +0.29, +0.06, -0.04, +0.17, -0.26 (M48: +0.97, +0.52, +0.42, +0.27, +0.04, -0.05, +0.23, 0.00). The dispersion buckets add 0.2-0.3% to the short 2022 weeks and leave the long weeks where M48 put them; 50-60 days in 2022 and 2023 is the one cell that is still negative for every method.

## 4. By DTE (all validation, vs recalibrated market)

| Method | 1-7 | 8-14 | 15-21 | 22-28 | 29-35 | 36-42 | 43-49 | 50-60 |
|---|---|---|---|---|---|---|---|---|
| M51 per dispersion | +0.60 | +0.55 | +0.59 | +0.66 | +0.70 | +0.86 | +1.07 | +0.32 |
| M52 rolling | +0.57 | +0.42 | +0.48 | +0.60 | +0.70 | +0.93 | +1.19 | +0.33 |
| M53 rolling per dispersion | +0.58 | +0.50 | +0.56 | +0.65 | +0.72 | +0.94 | +1.17 | +0.30 |
| M54 half-life | +0.46 | +0.43 | +0.49 | +0.58 | +0.65 | +0.84 | +1.17 | +0.23 |
| M56 two MLPs | +0.56 | +0.44 | +0.50 | +0.56 | +0.61 | +0.79 | +1.16 | +0.30 |
| M58 per market width | +0.61 | +0.54 | +0.58 | +0.66 | +0.70 | +0.86 | +1.08 | +0.33 |
| M62 market-shape weights | +0.32 | +0.42 | +0.52 | +0.61 | +0.70 | +0.94 | **+1.23** | +0.45 |
| M63 rn-shape weights | +0.25 | +0.34 | +0.44 | +0.61 | +0.71 | +0.97 | +1.17 | +0.23 |
| M57a seed bag alone | +0.11 | +0.01 | +0.08 | +0.20 | +0.37 | +0.65 | +0.97 | -0.22 |

The dispersion lever lifts the short weeks (M51 1-21 days +0.55% to +0.60% against M48's +0.44% to +0.53%); the refit lever lifts 36-49 days (M52 +0.93%, +1.19%). M53 has both. Feature-weighted laws are the flattest-rising: weakest at 1-7 days, strongest at 43-60.

## 5. By part of the distribution (zone Brier edge % vs recalibrated market, all DTE pooled)

| Method | Left wing | Left shoulder | Body | Right shoulder | Right wing |
|---|---|---|---|---|---|
| M51 per dispersion | +0.75 | +0.90 | +0.74 | +1.06 | +1.13 |
| M52 rolling | +0.83 | +0.84 | +0.68 | +1.08 | +1.16 |
| **M53 rolling per dispersion** | +0.80 | **+0.94** | +0.73 | **+1.13** | **+1.17** |
| M54 half-life | +0.76 | +0.79 | +0.61 | +1.03 | +1.14 |
| M55 zone bands | +0.78 | +0.61 | +0.56 | +0.94 | +1.14 |
| M56 two MLPs | +0.78 | +0.88 | +0.69 | +1.11 | +1.15 |
| M57a seed bag alone | +0.78 | +0.96 | +0.56 | +1.10 | +1.28 |
| M58 per market width | +0.75 | +0.89 | +0.73 | +1.07 | +1.12 |
| M61 linear pool | +0.51 | +0.67 | +0.18 | +0.90 | +1.06 |
| M62 market-shape weights | +0.72 | +0.93 | +0.69 | +1.05 | +1.10 |
| M63 rn-shape weights | +0.78 | +0.90 | +0.65 | +1.00 | +1.08 |

Right shoulder and right wing lead as always; M53 is the first method above +0.9% in the left shoulder and above +1.1% in both right zones at once. The zone-band document (M55), built to give each zone its own weights, is worse in every zone than the plain law: per-zone weights overfit the learning rows. The best (DTE week, zone) cells: right shoulder at 43-49 days (M56 +2.03%, M62 +2.01%, M53 +1.99%), then the left shoulder at 43-49 days (+1.4% to +1.6%). Per-region tables (14 regions) are in `edge_dte_zone_region.csv`.

## 6. By ticker (vs recalibrated market, twCRPS)

| Method | Tickers | Edge positive | t > 2 | t < -2 | Positive all 4 years (3 of 4) | (ticker, DTE week) t > 2 / t < -2 of 855 | (ticker, DTE, zone) t > 2 / t < -2 of 4,275 |
|---|---|---|---|---|---|---|---|
| **M51** | 110 | **91** | 71 | **5** | **21 (58)** | 393 / **36** | 1,853 / 77 |
| M52 | 110 | 84 | 69 | 8 | 20 (56) | 369 / 51 | 1,908 / 76 |
| M53 | 110 | 86 | 65 | 7 | 16 (57) | 367 / 47 | 1,819 / 77 |
| M54 | 110 | 84 | 69 | 10 | 12 (54) | 375 / 48 | 1,839 / 80 |
| M55 | 110 | 83 | 71 | 14 | 13 (57) | 379 / 64 | 1,621 / 124 |
| M56 | 110 | 87 | 67 | 8 | 16 (55) | 390 / 49 | **1,955 / 63** |
| M57a | 110 | 63 | 45 | 24 | 8 (33) | 211 / 118 | 1,385 / 152 |
| **M58** | 110 | **91** | **74** | 6 | 20 (61) | **398** / 39 | 1,881 / 74 |
| M59 | 110 | 90 | 68 | 10 | 17 (60) | 369 / 50 | 1,905 / 76 |
| M60 | 110 | 87 | 66 | 8 | 16 (53) | 387 / 45 | 1,874 / 80 |
| M61 | 110 | 84 | 66 | 10 | 15 (60) | 352 / 63 | 1,686 / 163 |
| M62 | 110 | 88 | 70 | 5 | 13 (56) | 372 / 42 | 1,752 / 80 |
| M63 | 110 | 78 | 65 | 15 | 12 (52) | 328 / 64 | 1,603 / 96 |

Round-4 reference, M48: 91 positive, 73 with t > 2, 7 with t < -2, 21 all four years, 413 / 44, 1,971 / 73.

- **Leading tickers, M53** (at least 1,000 rows): KKR +3.1% (t 9.8), CIEN +2.6%, INTU +2.2%, CNC +2.0%, CME +1.7%, ACN +1.7%. **Lagging:** CCJ -0.7% (t -2.9), GE -0.7%, CPB -0.6%, ENPH -0.6%, KHC -0.4%. M51 leads with KKR +2.8%, CIEN, CNC, INTU, CME, BMY and lags with ENPH -0.8%, GE, CCJ, CPB, FTNT -0.3%. The laggards' deficits have shrunk every round (ENPH: -1.2% at M29, -0.8% at M48, -0.6% at M53).
- Against the per-name baseline M51, M52 and M53 are positive on 98 of 110 tickers (86-87 with t > 2, 4 with t < -2).
- Strongest (ticker, DTE week, zone) cells (at least 500 rows): M52 AVGO 36-42 days left wing +3.8% (t 12.3), M56 BIDU 29-35 days right wing +3.1% (t 12.1), M53 KLAC 36-42 days left wing +5.3% (t 10.2), M52 JNJ 22-28 days left wing +1.5% (t 9.5).
- These counts are not adjusted for multiplicity. Per-ticker, per-year and per-cell tables are in the CSVs.

**Gate A0007** (beat the market and the per-name baseline in at least 3 of 4 years and 3 of 5 regimes), passes / expected by luck (luck 95th percentile), 831 cells:

| Market | M51 | M52 | M53 | M54 | M55 | M56 | M58 | M59 | M60 | M61 | M62 | M63 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Recalibrated | 233 / 36 (47) | 233 / 31 (42) | 200 / 37 (48) | **237 / 30 (41)** | 229 / 34 (43) | 232 / 34 (44) | 235 / 35 (45) | 226 / 30 (39) | 230 / 32 (42) | 226 / 38 (48) | 230 / 41 (51) | 210 / 42 (52) |
| Raw | 141 / 49 (60) | 137 / 48 (60) | 141 / 50 (62) | 148 / 46 (58) | 151 / 49 (60) | 157 / 49 (59) | 140 / 49 (60) | 130 / 47 (58) | 143 / 47 (58) | 158 / 49 (61) | 152 / 53 (63) | 161 / 56 (68) |

M51 and M58 beat the recalibrated market in all four years in 142 and 144 cells (M48: 145). M53's pooled edge is the highest and its gate (200) the lowest of the dispersion family: the 21-day refits move the per-cell edges around more than they add on average, and the cell test counts consistency, not size. M51 is the better candidate on the cell criterion; M53 on the pooled score.

**Calibration** (validation; nominal 5% below the 5th and 5% above the 95th percentile): M51 5.7% and 4.9%; M53 5.7% and 4.8%; M52 5.8% and 5.0%; M58 5.7% and 4.9%; M61 6.2% and 5.2%; the raw market 4.7% and 9.0%; the per-name baseline 8.5% and 10.1%.

## 7. What to focus on next

1. **Leading candidates remain M51 and M53:** M51 on the cell criterion and M53 on pooled score, with M48 and M29 as lineage. M57 underperformed, so the seed bag is an ingredient for a focused dispersion/rolling test, not an automatic replacement.
2. **Next experiment:** ADR-0277 proposes five small, registered variants around M51/M53: seed bag with the winning weight laws, two coarser dispersion partitions, and a slower refit schedule. The 2024-25 holdout still requires a separate owner opening.
3. **Stop:** new conditioning in the weight law (zone bands, strike density, vol level, risk-neutral shape moments, linear pools); seed spread (0.22%) is now the largest uncontrolled effect, so every future fit comparison needs three seeds or a bag.
4. **Needs an ADR (not JSON):** a shrunk per-ticker term in the weight law (the same five laggards in every stack); buckets on panel columns; the strike-node surface as a sequence input.

## 8. How this moves toward production

- **Candidate:** M51/M53, quantile averages of four CDFs with a weight table per dispersion bucket (and a 21-day refit schedule for M53), on a 3,273-parameter MLP over 90 Theta chain inputs; positive in every regime, 6-7 times luck at the gate, calibrated within one point of nominal, reproducible across seeds in the stack (the bag is the production model slot).
- **Serving shape unchanged:** four CDFs per row, a weight table, refits on a schedule, all expressible in the existing node's JSON.
- **Still missing:** the 2024-25 holdout, opened once by the owner; the recalibrated market inside the scorer; and a serving document. The three formerly stopped runs completed on 2026-10-22. Holdout and 2026 are untouched; no trading or P&L has been evaluated.

## Files

| Step | Document (`children/index_options/configs/`) | Output (pilot root `/home/russell/data/index_options/theta-strike-screen-20261018/`) |
|---|---|---|
| Fits wide MLP 64 seeds 29, 47 and 66/106-input MLP 64 | `run-strike-screen-fits-wf-{wide-mlp64-s29,wide-mlp64-s47,mlp64-d03,wider-mlp64}.json` | `fits/wf-*` (all four complete) |
| Score the fits | `run-strike-region-scores-wf-<tag>.json` | `scores/strike-region-scores-wf-*` (all four complete) |
| Methods M51-M65 | `run-edge-method-51..65-*.json`, `run-edge-method-57a-mlp64-seed-bag.json` | `scores/edge-method-5*-*/`, `scores/edge-method-6*-*/` (M57/M64/M65 complete) |
| Gate analyses | `docs/memos/data/2026-10-20-walkforward/analysis_wf.py --method-rows --candidate wide_mlp64` | `analysis/edge-method-5N-*/`, `analysis/wf-wide-mlp64-s{29,47}/` |
| Collected CSVs | `docs/memos/data/2026-10-20-edge-methods/` | `edge_*.csv[.gz]` cover M01-M65, including preserved historical rows after old score folders were offloaded; `edge_variant_*.csv` include the 66/106-input fits |

Breakdowns by ticker, DTE week and zone for every method: `edge_ticker_dte.csv.gz`, `edge_ticker_dte_zone.csv.gz`, `edge_ticker_zone.csv.gz`, `edge_ticker_year.csv.gz`, `edge_year_dte_zone.csv`, `edge_period_dte_zone.csv`, `edge_dte_zone_region.csv`; gates in `edge_gate_vs_market.csv`, `edge_gate_vs_calibrated_market.csv`, `edge_gate_summary.csv`; calibration in `edge_calibration.csv`. Packet 5 tally `registration/wall-used-p5.txt`; the round-5 orchestration log is in the session scratchpad (not evidence).


## 2026-10-22 completion: previously stopped runs

**Contract.** This resumes the approved round-5 packet on already committed, clean Theta-era assets. Path A0002/A0003/A0007/A0008 still control the score, yearly walk-forward, gate and warm-up treatment. The ranked comparison is 449,498 validation rows from 2020-23; 2019 is an unranked warm-up. All documents set `end_before: 2024-01-01`; the 2024-25 holdout and 2026 were not opened. There is no trade rule or P&L.

**Execution.** M57 used the existing three-seed bag M57a, completed in 156 s, and its A0007 analysis completed. The 66-input MLP 64 fit completed all five folds in 997 s (peak RSS about 8.0 GB), its scorer in 1,237 s, M64 in 300 s, and its analysis completed. Its scored-row SHA-256 is `33149634c53ce27f910818f9b92af3ce77e7aa59066b04a58a5844a504e6c2b4`. The 106-input MLP 64 fit completed all five folds in 1,040 s (peak RSS about 11.4 GB), its scorer in 1,258 s, M65 in 112 s, and its analysis completed. Its scored-row SHA-256 is `ede40a96ae867b464c8e6b73e6bf24a3a63dbec73df5be74b363bfe9638a1ceb`. M64/M65 ran from resolved document copies under pilot-root `documents/study/`; the repository placeholders retain zero source hashes and need resolution before reuse. Every completed command returned 0. Packet-5 wall tally is 9,554/14,400 s; `scores/` is 37,968,358,276/40,000,000,000 bytes; C: had 113 GiB free after these runs.

**Results.** Values are pooled twCRPS skill against the recalibrated market on the ranked 2020-23 rows, positive meaning better forecast accuracy. M57 (bag in the M16 recipe) scored +0.58167% (t 10.50), with 245/831 A0007 cells passing against 27.89 expected by luck. M64 (66 inputs, M16 recipe) scored +0.54946% (t 10.18), gate 241/831 against 25.23 luck. M65 (106 inputs, M16 recipe) scored +0.57846% (t 10.69), gate 236/831 against 26.87 luck. All three were positive in each year and each of five regimes, but M57 and M65 weakened to +0.21% and +0.14% in 2022. Their pooled scores are below the same recipe's 90-input M48 (+0.63806%), and below the dispersion leaders M51 (+0.68435%; gate 233) and M53 (+0.69034%; gate 200). Gate counts and pooled scores are different criteria; M57's higher gate count does not reverse its lower pooled score.

As standalone models, the 66-input fit scored +0.11294% against the recalibrated market (t 1.06; gate 111/831 against 63.35 luck); the 106-input fit scored +0.23925% (t 2.29; gate 117/831 against 65.40 luck). The 106-input fit looks better standalone, but each new architecture/input comparison has only one seed and the prior 90-input MLP 64 seeds ranged from -0.02% to +0.20%. These results do not isolate an input benefit from seed variation. No new blend displaced M51/M53.

**Handoff.** The existing comparison CSVs append M57/M64/M65 and the two fits while retaining rows for older score folders that were offloaded to free disk. Full outputs remain at pilot-root `fits/wf-mlp64-d03/`, `fits/wf-wider-mlp64/`, `scores/strike-region-scores-wf-mlp64-d03/`, `scores/strike-region-scores-wf-wider-mlp64/`, `scores/edge-method-{57-qa-mlp64-seed-bag,64-mlp64-66-inputs,65-wider-mlp64}/` and matching `analysis/` folders. Next: seek owner approval of proposed ADR-0277 before creating five config files or running another packet. The 2024-25 holdout remains sealed.
