## Question

What is the technical design of training and evaluation for the ADR-0270 strike-region screen, and how is performance measured against the market-implied curve and the baseline models? This reconciles the 2026-10-18 records (windows, market and baselines, features, architecture), the locked Path rows (A0001, A0003-A0005; A0002 unlocked) and what the 2026-10-19 run did. Predictability only: no premiums, costs, P&L or strategy economics.

## Finding

### 0. Binding decisions (Path)

- A0001 (locked): model the full conditional distribution in vol-standardized z; integrate it over any strike region; no fixed bins.
- A0002 (UNLOCKED, owner may lock): primary score is threshold-weighted CRPS on the strike zones (|z| about 0.5-2.5); Brier and reliability at the listed strikes; PIT/Berkowitz in the tails; only proper weighted scores; never choose the evaluation subset by outcomes.
- A0003 (locked): development 2017-01-03..2019-12-31; validation 2020-01-02..2023-12-31 (sub-periods S0-S4); holdout 2024-01-02..2025-11-28 sealed, opened once; 2026 sealed forever; label_reaches purge at every boundary.
- A0004 (locked): screen rung = warm-up fold (2017-18 to 2019, never ranked) + one scored fit through 2019; promotion rung = four yearly expanding retrains with seeds 11/29/47; holdout = one fold trained through 2023.
- A0005 (locked): eligible only if paired CRPS skill vs horizon_empirical and vs the market-implied reference is above zero on the full validation window and in >= 3 of 4 years; two-way cluster-robust intervals; never the best cell by score.
- Owner, 2026-10-19: apply the retrain-before-each-validation-year sequence to ALL events (not only promotion); ask whether the whole predictive distribution per (ticker, DTE) is good, because the strategy (condor, reverse condor, other) is not yet known and will be chosen by confidence in the prediction.

### 1. Forecast object

One data point per (ticker, quote day); one CDF per listed expiry <= 60 calendar days. Target z = log(P_T / P_0) / reference_scale, reference_scale = max(rv_22, 0.001) * sqrt(sessions_to_expiry); strike K maps to z_K = log(K / P_0) / reference_scale. Output: a 3-component Gaussian mixture on z (K=3). DTE enters as log_sessions_to_expiry and calendar_days_per_session. Expiries are 97% Fridays, 85% of gaps are 7 days, so DTE = weeks to the Friday expiry + quote weekday; 92 names list weeklies, 26 are monthly-only (expiry position, not day windows).

### 2. Training

- Inputs (arm context_chain, 66): 22 daily returns; 16 context scalars (DTE terms, momentum, realized vol levels, semivariance, liquidity); market quantile function rn_q (19, in z), interior mass and 8 chain scalars. The design's chain branch is 13 logit differences logit F_Q(z_k) - logit Phi(z_k), returns in vol units asinh(ret / rv_22) and a shared head: NOT yet built (the run used rn_q, raw returns, 116 per-ticker heads).
- Encoders: mlp 32 to 16, gru 16, tcn 16 (dilations 1-16), dlinear k3, PatchTST d32; head width 8; 10.6k-18.7k parameters; capacity cap min(100k, 0.25 x fit rows).
- Loss: L1 = crps + tail_crps (default); L3 = crps + decision_brier at the listed strikes (axis); full-distribution fit first, region-focused emphasis is a tested axis, not the default.
- Optimization: AdamW lr 3e-4, wd 0.01, dropout 0.1, batch 512, patience 10, ceiling 120, deterministic CUDA; seed 11 screen, 11/29/47 promotion. Monitor band = last cal_n = 82 origins of each fold's train window, purged. Observed: every fit stops at epoch 11-13 with the best epoch at 1-2 (overfit); levers: finer early stopping, wd 0.3, shared head.
- Folds (all events): warm-up 2017-18 to 2019 (timing, curves, not ranked), then yearly retrains: through 2019 to 2020, through 2020 to 2021, through 2021 to 2022, through 2022 to 2023. Holdout only for promoted recipes.
- Pre-ranking gates: 0 contract gates; 1 synthetic recoverability (passed, D 0.0197); 2 block-shift control (accepted by the owner, incomplete); 3 per-epoch curves.

### 3. Comparators (all scored on identical identities, sigma_ref and clock)

| Reference | Role | Status |
|---|---|---|
| market_raw | like-for-like competitor: Breeden-Litzenberger CDF from Theta EOD chains at the listed expiry | built (rn_q; scorer market column) |
| market_calibrated | market_raw after a 21-knot PIT map fit on fit-band rows (rate: variance premium) | NOT built; the production comparator |
| pooled standardized empirical | eligibility gate for "the model adds beyond scale" | built (scorer pooled) |
| per-name horizon_empirical | inherited low bar (ignores volatility clustering) | built (queue and scorer) |
| Student-t/EWMA, HAR scale, GARCH, tree quantile | optional ladder rungs | not built |

### 4. Measurement

- Headline per (ticker, expiry rank, fold or year): threshold-weighted CRPS on the strike zones and Brier at the listed strikes (decision_wing_twcrps, decision_strike_brier), with full-distribution CRPS, tail CRPS, PIT and reliability beside it. Region (14 z bins) Brier is a decomposition for diagnosis, pooled over tickers; per-ticker region cells are below the noise floor (median 1 event).
- Paired differences vs each comparator on identical identities; intervals: two-way cluster-robust (symbol, settlement date) and date-block bootstrap (blocks 30/60), wider reported.
- Replication: sign of the paired skill in >= 3 of 4 validation years and in sub-periods; families declared in advance, Holm within the primary family.
- Cell = (ticker, expiry rank) with weekly and monthly-only names separate; expiry windows are the 7-day ranks 1-8; the data-driven bins, if any, are chosen on 2019 only and frozen.
- Direction labels (descriptive, no P&L): model mass above or below the market in a region with realized frequency on the model's side: left tail = put-side, right tail = call-side, centre = body; the strategy then follows from where confidence and edge sit.

### 5. Where yesterday's notes and today's plan disagree

- DTE slices: the research record's test 6 uses 7-14, 15-28, 29-45, 46-60; the protocol and scoring documents use 1-7 ... 36-60; neither is derived. Replace by weekly expiry ranks (this document).
- The ADR cell is DTE slice x strike region x ticker; A0001/A0002 imply full-distribution cells with region-weighted scoring. This design makes (ticker, expiry rank) the cell and regions the decomposition. Needs the owner's confirmation and an ADR line.
- The eligibility gate in the notes is the pooled standardized empirical and market_calibrated, not horizon_empirical; the 2026-10-19 memo headlines horizon_empirical and raw market.
- The screen used one stale fit for 2020-2023; the design now retrains yearly.

### 6. Status of the 2026-10-19 screen (not production evidence)

20 matrix fits complete; vs the raw market the models lose on every proper score (0.8-3.3% weighted CRPS); a left-tail pocket at 36-60 DTE wins on region Brier (about +2%, both folds); BX and KKR beat the market per ticker in both folds; the raw market is far worse calibrated than the models (reliability 22-27%), which a recalibrated market would erase. No production candidate.

### 7. Sequence from here

1. Lock A0002 (owner). 2. Build market_calibrated; re-score. 3. Build the missing design pieces (logit chain vector, vol-unit returns, shared head, optional probit-PIT residual arm). 4. Rerun with the yearly walk-forward, all validation rows, one document per encoder. 5. Declare candidates by section 4; 6. holdout once for promoted cells; 7. registration and serving.

## Sources

- children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md and 2026-09-23-evaluation.md (Path A0001, A0002).
- 2026-10-18-strike-screen-windows-lead-time-cells.md; 2026-10-18-market-implied-reference-and-baselines.md; 2026-10-18-model-architecture-size-testing.md; 2026-10-18-theta-chain-feature-engineering.md.
- children/index_options/docs/memos/2026-10-19-strike-screen-matrix-results.md; ADR-0270 in docs/architecture/decision-log.md.
- Gneiting & Raftery 2007; Gneiting & Ranjan 2011; Diebold, Gunther & Tay 1998; Cameron, Gelbach & Miller 2011 (as cited in the records).
