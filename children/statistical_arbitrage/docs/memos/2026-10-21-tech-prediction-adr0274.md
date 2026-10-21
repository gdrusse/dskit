## TL;DR

Of the three model changes the research note ranked, **one meets the pre-registered rule on the primary next-session label**: fitting the lagged-spread model on a **vol-scaled label** (the pair-spread change divided by its own prior-only 60-session standard deviation, forecast multiplied back) lifts the ATM 20-session out-of-sample R² from **+0.199 to +0.242** against the expanding mean, wins four of five annual folds, and is BH-significant against the frozen baseline (DM t +2.86, p 0.002). The **Kalman-OU spread state** adds nothing on ATM (its direct forecast, aligned to the next-session label, scores +0.058 / +0.145 against the baseline's +0.081 / +0.199; fed to Ridge it ties) and beats the baseline on skew without significance (+0.011 / +0.053, DM p 0.20 / 0.24), and **pooling across a fifteen-name cohort** helps the 5-session ATM target marginally but collapses on the 20-session target (2021 fold R²oos −0.59), because the pooled fit mixes pair spreads of very different scale. Skew spreads remain unforecastable. Nothing here is a trade rule, P&L or edge.

## Execution contract

- **Authority:** ADR-0274 (proposed 2026-10-21, written before code under the owner's instruction to implement the research note and run it) on top of ADR-0272/0273; next-session label only; folds, clock, refit cadence, drop rules, tests as before.
- **(A) scaled label:** `ys_h = y_h / sd60`, `sd60` the prior-only 60-session sample sd of the target spread (`PairPanelFeatures`, a `ComputeFields` quotient); `StandardizedEstimator(Ridge)` on the frozen `lagged` features; the forecast is multiplied back by `sd60` and scored in raw units. Alpha from {0.1, 1, 10, 100} on the 2020 fold: 100 for both targets.
- **(B) Kalman-OU:** new node `KalmanSpreadState` (random-walk level + AR(1) deviation + measurement noise; four parameters by maximum likelihood on the 252 sessions before each of the seven ADR-0271 fit dates; filter through every session). `kalman_direct` = `φ·(φ^h − 1)·x̂_t`, the model's expectation of the next-session label (`label_offset` 1, ADR-0274 Amendment 1a: the first round used `(φ^h − 1)·x̂_t`, the change from `t`, which the correctness review caught; the (B) cells were rerun); `kalman_ridge` = Ridge on `lagged` + deviation + expected change (alpha 100 both targets). A fit whose deviation sd falls below 1e-3 of the window's difference sd, or whose φ sits at a bound, is degenerate and the previous parameters are carried (Amendment 1b).
- **(C) cohort pooling:** rule-selected twelve roots (largest 2016–2020 median quoted contracts in the strike-screen census, quoted from 2018-01-02 to 2025-12-15, excluding the three names and GOOG): ISRG, CMG, BA, GS, CHTR, CAT, AVGO, BIDU, ABBV, GILD, CRM, HUM. New surface pass `run-tech-structural-universe.json` (hash `d94c3d0d…803a`, run `…-e6d7106e`, 405 s, 30,000 rows: fifteen names × 2,000 sessions, one `no_forward` day each for AMZN and GOOGL; artifact SHA-256 `69d843a29dae…`). 105 pairs; `pooled` = one `StandardizedEstimator(Ridge)` across all pairs at a 52-week cadence (six fits inside the data, 41,139 → 171,199 training rows; the seventh block starts after the data end), `pair_yearly` = the same per pair at the same cadence (the fair baseline), `blend` = w·pooled + (1−w)·pair_yearly; scored on the three phase-one pairs only. Dev picks: pooled alpha 100 (both), pair_yearly 0.1 (ATM) / 10 (skew), w 0.75 (ATM) / 0.5 (skew).
- **Family per cell (BH 5%):** as run, the three-pair cell's family holds `scaled`, `kalman_direct`, `kalman_ridge` versus `lagged` and each model versus `mean` including `lagged` itself (seven hypotheses); the cohort cell's holds `pair_yearly`, `pooled`, `blend` versus `lagged`, `pooled`/`blend` versus `pair_yearly`, and the versus-`mean` gaps (nine). ADR-0274 pre-registered ten per cell; a reviewer re-ran BH over the literal ten and every versus-`lagged` decision is unchanged (Amendment 1c). "Improves" = R²oos above the baseline, BH-rejected, four of five folds.
- **Rounds (disclosed):** round 1: a first development attempt failed at run time in the 20-session cell because the generator chained the 5-session forecast columns into it (its run directory was removed before the rerun; only the log line survives), then development `906ee320…e968` (run `…-955c3959`) and validation `ec5d7083…a5b` (run `…-cd52dbf9`, artifacts under `validation/`). Round 2, after the Kalman alignment fix: development `345f6ebc…4b8a` (run `…-6e5d99ae`, `development-r2/`) and validation `e9d0f6a1…76ad` (run `…-a84fba33`, `validation-r2/`); the selection is identical in both rounds and every (A) and (C) number below is byte-identical across rounds; only the (B) rows changed. On the 2020 fold the scaled label looked *worse* than the baseline (ATM 0.034 vs 0.097), the pooled model better (0.120), and the blend best (0.128); validation reversed the first two readings, a reminder of how thin one development year is.
- **Commands** (worktree root, `PYTHONPATH=children/statistical_arbitrage:.`, `/home/russell/dskit/.venv/bin/python`): `python -m dskit.pipeline run children/statistical_arbitrage/configs/run-tech-structural-universe.json --asof 2026-10-21`, then `run-tech-prediction-adr0274-{development,validation}.json` (the committed documents are round 2).

## Implementation evidence

One generic node (`libs/kalman_spread.py:KalmanSpreadState`, 9 unit tests: simulated OU parameters recovered within tolerance, prior-only windows, the forecast formula with and without a label offset, a hole propagating the state, a degenerate first fit, a later degenerate fit carrying the previous parameters, the deviation-variance floor, default-deny), documents generated from one scratch generator and committed as JSON, child pins (cohort rule reproduced from the census, same surface params as ADR-0271, three-pair scoring filter, cadences, frozen selection; 39 pins pass), READMEs and trees updated. Purity and conformance gates pass except main's known reds.

## Validation 2021–2025, next-session label, three pairs

Pooled R²oos against the expanding mean; folds 2021–2025; DM versus `lagged` with lags h−1.

| Cell | Model | R²oos | IC | folds | DM t / p | BH |
|---|---|---:|---:|---|---:|---|
| ATM 5 | lagged | +0.081 | +0.228 | +0.025 / +0.044 / +0.058 / +0.118 / +0.114 | — | — |
| ATM 5 | scaled | +0.087 | +0.233 | +0.068 / +0.026 / +0.062 / +0.128 / +0.122 (4/5) | +0.89 / 0.186 | no |
| ATM 5 | kalman_direct | +0.058 | +0.197 | +0.028 / +0.027 / +0.002 / +0.122 / +0.082 (2/5) | −2.00 / 0.977 | no |
| ATM 5 | kalman_ridge | +0.076 | +0.220 | +0.044 / +0.042 / +0.050 / +0.103 / +0.113 (1/5) | −1.31 / 0.906 | no |
| ATM 20 | lagged | +0.199 | +0.464 | +0.065 / +0.095 / +0.247 / +0.323 / +0.112 | — | — |
| ATM 20 | **scaled** | **+0.242** | +0.483 | +0.181 / +0.051 / +0.301 / +0.399 / +0.118 (**4/5**) | **+2.86 / 0.002** | **yes** |
| ATM 20 | kalman_direct | +0.145 | +0.377 | +0.036 / +0.025 / +0.060 / +0.361 / +0.110 (1/5) | −1.67 / 0.953 | no |
| ATM 20 | kalman_ridge | +0.190 | +0.453 | −0.026 / +0.063 / +0.213 / +0.395 / +0.085 (1/5) | −0.65 / 0.743 | no |
| Skew 5 | lagged | +0.005 | +0.129 | +0.038 / −0.001 / −0.003 / +0.017 / +0.024 | — | — |
| Skew 5 | scaled | +0.005 | +0.132 | +0.047 / −0.006 / −0.003 / +0.021 / +0.025 (3/5) | +0.10 / 0.462 | no |
| Skew 5 | kalman_direct | +0.011 | +0.134 | +0.027 / +0.006 / +0.005 / +0.025 / +0.033 (4/5) | +0.84 / 0.199 | no |
| Skew 5 | kalman_ridge | +0.005 | +0.127 | +0.047 / −0.002 / −0.005 / +0.021 / +0.028 (3/5) | −0.19 / 0.577 | no |
| Skew 20 | lagged | +0.034 | +0.256 | +0.105 / +0.027 / +0.006 / +0.049 / +0.073 | — | — |
| Skew 20 | scaled | +0.035 | +0.251 | (3/5) | +0.07 / 0.471 | no |
| Skew 20 | kalman_direct | +0.053 | +0.251 | +0.071 / +0.041 / +0.039 / +0.071 / +0.094 (4/5) | +0.72 / 0.237 | no |
| Skew 20 | kalman_ridge | +0.035 | +0.243 | +0.094 / +0.012 / +0.007 / +0.074 / +0.085 (3/5) | −0.07 / 0.530 | no |

**(A)** The scaled label passes all three conditions on ATM 20-session and nowhere else. Per pair it helps AAPL–AMZN (+0.248 → +0.328) and AMZN–GOOGL (+0.153 → +0.199) and slightly hurts AAPL–GOOGL (+0.178 → +0.164); the one losing fold is 2022 (+0.051 vs +0.095). `skill_vs_mean` passes (pooled DM t 4.92, across-fold t 3.33). The comparison bundles two changes, standardized features and the scaled label: a reviewer's control (standardized Ridge, alpha 100, raw label, same rows and blocks) scores +0.211, so about +0.012 of the +0.043 is standardization and +0.031 the label scaling. The plausible mechanism is heteroskedasticity (the raw-label fit is dominated by high-variance episodes; the gain sits in the quiet years 2021 and 2023–2025), untested beyond that split. The alpha sits at the grid edge on the development fold, and validation is insensitive to it (+0.242 at 100, +0.244 at 0.1–10).

**(B)** With the label-aligned forecast the filter's own expectation is a weaker version of the Z: the fitted autoregressive coefficient swings between years (AAPL–AMZN φ 0.77, 0.97, 0.93, 0.34, 0.91, 0.90; half-lives 0.6–20 sessions), so a 20-session expectation built from one year's φ misses when the next year's reversion speed differs. On skew two 2022 windows have too few usable sessions to fit (55 of the 126 required; the previous parameters are carried) and two 2023 fits hit the deviation-variance floor and are carried too. Fed to Ridge the state is redundant with Z (coefficients split the same information). The 60-session Z is the more robust statistic of the same thing.

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
- R²oos in the tables is the pair-pooled row-level value (`skill_*.jsonl`, group `all`); the per-date `skill_vs_mean` block and the DM tests average across pairs per date, so a model can show a higher row-pooled R² and a negative DM t when outcomes are missing unequally across pairs (skew `kalman_direct`, 20-session). The cohort rule conditions on the last quote date, a survivorship condition acceptable for training material.

## Reproducibility and handoff

- Artifacts: `/home/russell/data/statistical_arbitrage/prediction-adr0274-20261021/{development,validation,development-r2,validation-r2}/` (`rows_*`, `fits_*`, `skill_*`, and in the validation directories `kalman_{atm,skew}.{jsonl,json}`); the round-2 `.json` summary SHA-256 prefixes are ATM h5 `ced8a17b5bce`, ATM h20 `fae8f2557d1b`, skew h5 `5035e1436b9c`, skew h20 `05c652c8b156`, cohort ATM h5 `8b22b392a9e0`, ATM h20 `0c2ac8965734`, skew h5 `0ac824960302`, skew h20 `971af6b8f48c` (the cohort summaries are identical across rounds). Config digests (round 2): universe pass `584b7d3bd691…`, development `7040852f9453…`, validation `2ddb9cae8f35…`, selection `5dc41a47a9e0…` (its `development_document_hash` is the document identity hash, not the file digest). The universe pass has no member pin; its `resolved.json` records the 459 planned files and reader fingerprint `5c3d7bf1…`, and the study documents pin the written artifact's digest. Every write node declares `overwrite: false`.
- Review record and next authorized action: filled at closure in the lane note; this memo authorizes nothing.
