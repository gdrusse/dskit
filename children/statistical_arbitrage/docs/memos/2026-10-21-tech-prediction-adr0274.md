## TL;DR

Of the three model changes the research note ranked, **one meets the pre-registered rule on the primary next-session label**: fitting the lagged-spread model on a **vol-scaled label** (the pair-spread change divided by its own prior-only 60-session standard deviation, forecast multiplied back) lifts the ATM 20-session out-of-sample R² from **+0.199 to +0.242** against the expanding mean, wins four of five annual folds, and is BH-significant against the frozen baseline (DM t +2.86, p 0.002). The **Kalman-OU spread state** adds nothing (its direct forecast is worse; fed to Ridge it ties), and **pooling across a fifteen-name cohort** helps the 5-session ATM target marginally but collapses on the 20-session target (2021 fold R²oos −0.59), because the pooled fit mixes pair spreads of very different scale. Skew spreads remain unforecastable. Nothing here is a trade rule, P&L or edge.

## Execution contract

- **Authority:** ADR-0274 (proposed 2026-10-21, written before code under the owner's instruction to implement the research note and run it) on top of ADR-0272/0273; next-session label only; folds, clock, refit cadence, drop rules, tests as before.
- **(A) scaled label:** `ys_h = y_h / sd60`, `sd60` the prior-only 60-session sample sd of the target spread (`PairPanelFeatures`, a `ComputeFields` quotient); `StandardizedEstimator(Ridge)` on the frozen `lagged` features; the forecast is multiplied back by `sd60` and scored in raw units. Alpha from {0.1, 1, 10, 100} on the 2020 fold: 100 for both targets.
- **(B) Kalman-OU:** new node `KalmanSpreadState` (random-walk level + AR(1) deviation + measurement noise; four parameters by maximum likelihood on the 252 sessions before each of the seven ADR-0271 fit dates; filter through every session). `kalman_direct` = `(φ^h − 1)·x̂_t`; `kalman_ridge` = Ridge on `lagged` + deviation + expected change (alpha 100 both targets).
- **(C) cohort pooling:** rule-selected twelve roots (largest 2016–2020 median quoted contracts in the strike-screen census, quoted from 2018-01-02 to 2025-12-15, excluding the three names and GOOG): ISRG, CMG, BA, GS, CHTR, CAT, AVGO, BIDU, ABBV, GILD, CRM, HUM. New surface pass `run-tech-structural-universe.json` (hash `d94c3d0d…803a`, run `…-e6d7106e`, 405 s, 30,000 rows: fifteen names × 2,000 sessions, one `no_forward` day each for AMZN and GOOGL; artifact SHA-256 `69d843a29dae…`). 105 pairs; `pooled` = one `StandardizedEstimator(Ridge)` across all pairs at a 52-week cadence (seven fits, 41,139 → 171,199 training rows), `pair_yearly` = the same per pair at the same cadence (the fair baseline), `blend` = w·pooled + (1−w)·pair_yearly; scored on the three phase-one pairs only. Dev picks: pooled alpha 100 (both), pair_yearly 0.1 (ATM) / 10 (skew), w 0.75 (ATM) / 0.5 (skew).
- **Family per cell (BH 5%):** three-pair cell: `scaled`, `kalman_direct`, `kalman_ridge` versus `lagged` and versus `mean`; cohort cell: `pair_yearly`, `pooled`, `blend` versus `lagged`, and `pooled`/`blend` versus `pair_yearly`, plus versus `mean`. "Improves" = R²oos above the baseline, BH-rejected, four of five folds.
- **Development attempts (disclosed):** two. The first failed at the 20-session cell because the generator chained the 5-session forecast columns into it (a wiring bug, no result read); the second (hash `906ee320…e968`, run `…-955c3959`) is the selection's basis. On that 2020 fold the scaled label looked *worse* than the baseline (ATM 0.034 vs 0.097), the pooled model better (0.120), and the blend best (0.128); validation reversed the first two readings, a reminder of how thin one development year is.
- **Commands** (worktree root, `PYTHONPATH=children/statistical_arbitrage:.`, `/home/russell/dskit/.venv/bin/python`): `python -m dskit.pipeline run children/statistical_arbitrage/configs/run-tech-structural-universe.json --asof 2026-10-21`, then `run-tech-prediction-adr0274-{development,validation}.json`. Validation hash `ec5d7083…a5b`, run `…-cd52dbf9`.

## Implementation evidence

One generic node (`libs/kalman_spread.py:KalmanSpreadState`, 5 unit tests: simulated OU parameters recovered within tolerance, prior-only windows, forecast formula, holes and degenerate fits, default-deny), documents generated from one scratch generator and committed as JSON, child pins (cohort rule reproduced from the census, same surface params as ADR-0271, three-pair scoring filter, cadences, frozen selection; 39 pins pass), READMEs and trees updated. Purity and conformance gates pass except main's known reds.

## Validation 2021–2025, next-session label, three pairs

Pooled R²oos against the expanding mean; folds 2021–2025; DM versus `lagged` with lags h−1.

| Cell | Model | R²oos | IC | folds | DM t / p | BH |
|---|---|---:|---:|---|---:|---|
| ATM 5 | lagged | +0.081 | +0.228 | +0.025 / +0.044 / +0.058 / +0.118 / +0.114 | — | — |
| ATM 5 | scaled | +0.087 | +0.233 | +0.068 / +0.026 / +0.062 / +0.128 / +0.122 (4/5) | +0.89 / 0.186 | no |
| ATM 5 | kalman_direct | +0.015 | +0.191 | +0.026 / +0.007 / −0.116 / +0.127 / +0.033 (1/5) | −3.74 / 1.000 | no |
| ATM 5 | kalman_ridge | +0.076 | +0.221 | +0.043 / +0.042 / +0.050 / +0.104 / +0.112 (1/5) | −1.39 / 0.917 | no |
| ATM 20 | lagged | +0.199 | +0.464 | +0.065 / +0.095 / +0.247 / +0.323 / +0.112 | — | — |
| ATM 20 | **scaled** | **+0.242** | +0.483 | +0.181 / +0.051 / +0.301 / +0.399 / +0.118 (**4/5**) | **+2.86 / 0.002** | **yes** |
| ATM 20 | kalman_direct | +0.128 | +0.379 | +0.024 / +0.011 / +0.046 / +0.380 / +0.027 (1/5) | −2.14 / 0.984 | no |
| ATM 20 | kalman_ridge | +0.194 | +0.454 | +0.004 / +0.068 / +0.211 / +0.395 / +0.087 (1/5) | −0.38 / 0.649 | no |
| Skew 5 | lagged | +0.005 | +0.129 | +0.038 / −0.001 / −0.003 / +0.017 / +0.024 | — | — |
| Skew 5 | scaled | +0.005 | +0.132 | +0.047 / −0.006 / −0.003 / +0.021 / +0.025 (3/5) | +0.10 / 0.462 | no |
| Skew 5 | kalman_direct | −0.012 | +0.122 | (0/5 above) | −1.75 / 0.960 | no |
| Skew 5 | kalman_ridge | +0.001 | +0.126 | (2/5) | −1.60 / 0.946 | no |
| Skew 20 | lagged | +0.034 | +0.256 | +0.105 / +0.027 / +0.006 / +0.049 / +0.073 | — | — |
| Skew 20 | scaled | +0.035 | +0.251 | (3/5) | +0.07 / 0.471 | no |
| Skew 20 | kalman_direct | +0.040 | +0.237 | (3/5) | −0.41 / 0.659 | no |
| Skew 20 | kalman_ridge | +0.035 | +0.235 | (2/5) | −0.06 / 0.523 | no |

**(A)** The scaled label passes all three conditions on ATM 20-session and nowhere else. Per pair it helps AAPL–AMZN (+0.248 → +0.328) and AMZN–GOOGL (+0.153 → +0.199) and slightly hurts AAPL–GOOGL (+0.178 → +0.164); the one losing fold is 2022 (+0.051 vs +0.095). `skill_vs_mean` passes (pooled DM t 4.92, across-fold t 3.33). The mechanism is heteroskedasticity: the raw-label Ridge is dominated by high-variance episodes, the scaled fit weights sessions equally; the gain is in the quiet years 2021 and 2023–2025.

**(B)** The filter's own forecast is poor and unstable: the fitted autoregressive coefficient swings between years (AAPL–AMZN φ 0.77, 0.97, 0.93, 0.34, 0.91, 0.90; half-lives 0.6–20 sessions; skew fits degenerate in 2022 for two pairs and `sigma_x` collapses to zero for one), so a 20-session expectation built from one year's φ is far off when the next year's reversion speed differs. The 60-session Z is the more robust statistic of the same thing. Fed to Ridge the state is redundant with Z (coefficients split the same information).

## Cohort pooling (C), scored on the three pairs

| Cell | Model | R²oos | folds | vs lagged t / p | vs pair_yearly t / p (BH) |
|---|---|---:|---|---:|---:|
| ATM 5 | lagged (weekly) | +0.081 | — | — | — |
| ATM 5 | pair_yearly | +0.076 | −0.027 / +0.035 / +0.057 / +0.120 / +0.114 | −2.54 / 0.994 | — |
| ATM 5 | pooled | +0.085 | −0.009 / +0.039 / +0.065 / +0.140 / +0.114 | +0.61 / 0.270 | +1.80 / 0.036 (no) |
| ATM 5 | blend 0.75 | +0.084 | −0.009 / +0.040 / +0.064 / +0.136 / +0.115 | +0.61 / 0.271 | +2.22 / 0.013 (**yes**) |
| ATM 20 | lagged (weekly) | +0.199 | — | — | — |
| ATM 20 | pair_yearly | +0.189 | −0.094 / +0.052 / +0.270 / +0.359 / +0.101 | −0.97 / 0.835 | — |
| ATM 20 | pooled | +0.052 | −0.587 / −0.247 / +0.251 / +0.383 / −0.099 | −3.49 / 1.000 | −3.78 / 1.000 (no) |
| ATM 20 | blend 0.75 | +0.113 | −0.403 / −0.136 / +0.275 / +0.394 / −0.023 | −2.63 / 0.996 | −2.88 / 0.998 (no) |
| Skew 5 | pooled / blend | +0.006 / +0.010 | — | +0.19 / 0.425; +1.58 / 0.057 | no / no |
| Skew 20 | pooled / blend | +0.012 / +0.031 | — | −1.51 / 0.935; −0.36 / 0.640 | no / no |

Pooling across the cohort beats the equal-cadence per-pair model on ATM 5-session (the blend is BH-rejected against `pair_yearly`, +0.009 R²oos) but not the weekly baseline, and it fails badly on ATM 20-session in 2021–2022 and 2025. The cause is visible in the design: the pooled Ridge fits one slope on raw vol-point labels across pairs whose spreads differ several-fold in scale (CMG–BIDU against AAPL–GOOGL), so the shared slope is wrong for the quiet mega-cap pairs in calm years. That is exactly what (A) fixes within a pair. The combination, pooled fitting on scaled labels, was not pre-registered and is the obvious next registration; it is not run here. The weekly-versus-yearly cadence also costs the per-pair model about 0.01 R²oos (pair_yearly below lagged in both ATM cells), so the cadence constraint matters.

## What this does and does not say

- A better yardstick for the same signal, not a new signal: the scaled label changes how the two lagged features are weighted, and the gain is concentrated where the baseline was weakest (2021). It is one BH-rejected comparison in a family of three per cell, pre-registered, on five folds; a separately registered confirmation on new data is the honest next step before anyone leans on +0.24.
- Measured mid-surface spreads, next-session label, no costs, no fills; the v6 replay already showed that forecastability of this spread did not convert into P&L at NBBO.
- The cohort result is a negative about *this* pooling design (raw labels, shared slope, yearly cadence), not about pooling; the per-pair models on cohort rows reproduce the three-pair baseline exactly, so the plumbing is right.
- The Kalman node is kept as a diagnostic tool (parameter paths are themselves informative about regime changes in reversion speed); it is not a forecaster here.

## Reproducibility and handoff

- Artifacts: `/home/russell/data/statistical_arbitrage/prediction-adr0274-20261021/{development,validation}/` (`rows_*`, `fits_*`, `skill_*`, and in validation `kalman_{atm,skew}.{jsonl,json}`); validation summary SHA-256 prefixes ATM h5 `8e7673b5316c`, ATM h20 `1a89825a21ad`, skew h5 `73400848c74c`, skew h20 `4136f0161e76`, cohort ATM h5 `8b22b392a9e0`, ATM h20 `0c2ac8965734`, skew h5 `0ac824960302`, skew h20 `971af6b8f48c`. Config digests: universe pass `584b7d3bd691…`, development `198048c6a627…`, validation `516ac0ed70a5…`, selection `bfd9b0ec4e8d…`. Every write node declares `overwrite: false`.
- Review record and next authorized action: filled at closure in the lane note; this memo authorizes nothing.
