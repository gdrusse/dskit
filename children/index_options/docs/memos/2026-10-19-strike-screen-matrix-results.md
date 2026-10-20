# ADR-0270 strike screen: data build, fits and first results (2026-10-19)

## TL;DR

Against the market-implied curve the models do NOT win overall: on the proper scores (CRPS family, strike Brier) the market is better in every cell, by 0.8-3.3% weighted CRPS. One replicated pocket stands out: left-tail regions (z from -2.0 to -1.0) at 36-60 DTE, where models beat the raw market's region Brier by about 1-2% in both folds. The raw market curve is badly calibrated and a recalibrated market is not built, so no cell is a production candidate yet.

## Execution contract

- Predictability only; no P&L, no trading. Holdout 2024-2025 sealed and never read; 2026 sealed forever. Development 2017-2019, validation 2020-2023.
- Data point = (ticker, day, listed expiry <= 60 DTE) with DTE as a feature; target z = log(P_T/P_0) / (rv_22 sqrt(sessions)); frozen Theta export of 427 files (24.6 GB), 118 names.
- Design source: `docs/research/distribution-modeling/2026-10-18-*.md`. Protocol `configs/strike-screen-protocol.json` revision 5, sha256 073d39cb4574de9e50c27f30a68a4d9c5e7e0a3dcca881bd5cb8af04f29edf78.
- Pilot root: /home/russell/data/index_options/theta-strike-screen-20261018 (not in git). Packet wall 14,400 s; used about 10,700 s plus an unrecorded scoring attempt.

## What ran

| Stage | Result |
|---|---|
| Converter (`ThetaExactExpiryTables`) | 118 names, 189.8M rows read, 1.22M surface rows, 280 s. Two defects fixed on the way: newest-first row groups in the export (reader `order_by`), a 360x reverse-split factor (snap). |
| Onboarding, raw-chain prepare, panel | Prepare vectorized and parallel (2,271 s killed, then 42 s with 6 workers); panel 1,212,826 rows x 321 columns, sha256 186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144; panel build peaked at 13.5 GiB (waived). |
| Stage 0 | Fold 1 (fit 2017-01..2018-08, validation 2019): 260,392 fit / 160,754 validation rows, 116 names (COHR, ECHO refused). Fold 2 (fit to 2019-08, validation 2020-2023): 426,603 / 706,003, 116 names (COHR, LLY refused; LLY has no chain rows 2019-09 to 2020-02). |
| Leakage control | Block-shift (120 sessions) kept 19% of rows; 2 of 8 cells completed, +7.5% and +5.8% against its own starved baseline, +2.0% and +0.1% against a full-band baseline. Accepted by the owner as the leakage evidence. |
| Speed | Training path was launch-bound; three bit-identical changes (no per-step host syncs, device index tensors, one CUDA graph) cut an epoch from 4.7 s to 1.0 s (PatchTST 5.9 to 1.3). |
| L1 and L3 fits (raw price columns plus a 119-column chain block, 2 encoders, 2 losses) | 16 of 16 complete, early stop at epochs 11-13 (best 1-3). Per-row score shards kept; skill NOT read; superseded by the matrix. |
| Matrix fits (this design) | 20 of 20 complete in 3,060 s: arms `context` (22 raw returns + 16 scalars incl. log sessions and calendar days per session) and `context_chain` (+ 19 market quantiles rn_q in z units, `rn_interior_mass`, 8 chain scalars) x mlp, gru, tcn, dlinear, patchtst_hf32 x 2 folds; crps + tail_crps, K=3. |
| StrikeRegionScores over the matrix | Attempt 1 failed (unseen-horizon KeyError, fixed `bb08f71b`); attempt 2 stopped (1.98 GB partial); attempt 3 with `validation_band_sample` 50,000 (`d01cc082`) complete: 21 min, 1.50 GB rows, 20 of 20 cells, no refusals. |

## Results available (model vs per-name empirical baseline, weighted CRPS skill; NOT the market)

| Arm / encoder | Fold 1 (2019) | Fold 2 (2020-2023) | Fold 2, DTE 36-60 |
|---|---|---|---|
| context / dlinear | +0.13% | +1.65% | +2.4% |
| context / gru | +0.26% | +1.18% | +1.9% |
| context / mlp | +0.46% | +1.35% | +2.1% |
| context / patchtst32 | +0.19% | +1.51% | +2.3% |
| context / tcn | +0.34% | +1.60% | +2.2% |
| context_chain / dlinear | +0.22% | +2.13% | +2.6% |
| context_chain / gru | +0.63% | +1.70% | +2.2% |
| context_chain / mlp | +0.60% | +1.96% | +2.4% |
| context_chain / patchtst32 | +0.66% | +1.98% | +2.6% |
| context_chain / tcn | **+0.90%** | **+2.23%** | **+2.8%** |

- Rows: 160,754 (fold 1) and 706,003 (fold 2) validation rows per cell; shard-level means over ALL validation rows (the scorer section below uses a 50,000-row sample and market-eligible rows).
- Fold 1: short horizons (1-21 DTE) are at or below the baseline for most cells; only 36-60 DTE is clearly positive (+1.1% to +2.0%). Fold 2: all DTE slices positive; 1-7 DTE +0.4% to +1.9%.
- The chain arm beats context-only by 0.2-0.8 points in fold 2 for every encoder; encoder choice matters less than the chain inputs.
- Every cell overfits fast: best monitor epoch 1-2, training loss falls, monitor loss rises from there (per-epoch curves are in each checkpoint `state.json`). Per-ticker heads (116) are the likeliest memorizer.

## Model versus the market-implied curve (StrikeRegionScores, validation band, 20 cells)

Scoring ran (21 min, 7.1 GiB peak) on a deterministic stride sample of 50,000 validation rows per cell (the full 8.7M-row evidence would exceed the 2 GiB file, 4 GB class and wall caps), market-eligible rows only (59% in fold 1, 73% in fold 2 of the sample). Differences are model minus market; standard errors take the wider of symbol and quote-date clustering. Not corrected for multiple testing.

| Proper score, all horizons | Fold 1 (2019) | Fold 2 (2020-23) |
|---|---|---|
| market vs per-name baseline (weighted CRPS) | +3.2% | +3.2% |
| best model vs baseline (context_chain / tcn) | +1.0% | +2.3% |
| model vs market, range over the 10 models | -1.9% to -3.3% | -0.8% to -1.9% |

- The market beats the model on weighted CRPS, CRPS, tail CRPS, strike Brier and wing twCRPS: the model is better in 0-4 of 50 cells per metric and fold, none at t > 3. The gap is smallest for the chain arm and for TCN (fold 2, context_chain/tcn: -0.8%).
- Region Brier (14 z regions x 5 horizons x 20 cells x 2 folds = 1,400 tests): model better in 221 of 700 cells (fold 1) and 351 of 700 (fold 2); t > 3 in 6 and 21 cells; market better at t < -3 in 12 of 700 (fold 2). Under no effect about 1 cell per fold would pass t > 3.
- Where the model wins (fold 2, 36-60 DTE): regions z in [-2.5, -1.0] and [1.5, 2.0], +0.9% to +2.1% mean advantage; strongest region 3 (z -2.0 to -1.5): +2.05%, t about 4.9 in 10 of 20 cells. Fold 1 shows the same pocket (regions 3-4 at 36-60 DTE: +1.9%, +2.1%). Cells are the same 116 names scored by 20 correlated models, so this is one pattern, not 21 independent findings.
- Where the market wins: short horizons and the far tails (1-7 DTE: region 0 -8%, region 13 -5% to -14%); the central region around the median at all horizons is a tie or market.
- Reliability (squared calibration gap): the models are 22-27% better than the market in about 95% of cells, t about 25. The raw risk-neutral curve is miscalibrated against realized frequencies (the variance risk premium). This is NOT a model edge to bank: a PIT-recalibrated market (market_calibrated, 2-6 parameters, +2.75% on ETFs in the research record) is the right production comparator and is not built.
- Per name (chain arm, five encoders averaged per row, weighted CRPS vs the raw market on the sampled market-eligible validation rows, 109 names, date-clustered t): the model beats the market in both folds on 13 names, about what chance gives (29-33% of names win per fold). Two stand out in both folds at t > 3: **KKR** (fold 1 +9.9%, t 3.6; fold 2 +6.6%, t 4.6) and **BX** (+7.0%, t 6.8; +5.1%, t 4.9). **COST** (+9.2% / +4.0%), **AMAT**, **COF**, **BLK**, **APO** (+9.4% / +1.3%) and **JPM** (+7.8% / +0.8%) are positive in both folds but fade in fold 2. Four names have t > 3 in fold 2, thirteen in fold 1. The leaders are alternative managers and large financials, a coherent group worth a pre-registered test, with the same calibrated-market caveat. Table: scratchpad `per_name_vs_market.csv` (not durable).

## Not done and caveats

- Per-sub-period (COVID) and per-year cuts are in `cells.parquet` but unread; ticker-group cuts are empty (no liquidity-tercile groups); the final promising-cell rule needs market_calibrated.
- Deviations from the design (each needs code): raw returns instead of asinh(ret/rv_22); the 19 rn_q quantiles instead of the 13 logit differences logit F_Q(z_k) - logit Phi(z_k); one head per ticker instead of a shared head; no probit-PIT residual arm.
- Scoring used a validation sample (50,000 rows/cell), not every row; fit outputs (about 1.2 GB for 20 cells) counted as evidence-class output by assumption; the 8 GiB host-RSS cap was breached in the panel build (waived), the L1/L3 and timing queues' scoring phase.
- Holdout conversion (2024-2025) not run; market-calibrated reference stage not run. Packet wall used about 12,550 s of 14,400.
- Test state: lane suites green; `tests/pipeline` has one purity failure that predates this lane (`dskit.onboarding.snapshot` import in `ParquetStreamRows`, on main).

## Artifacts

- Fits: pilot root `fits/matrix` (20 cells, checkpoints, per-row score shards), `fits/L1`, `fits/L3`, `fits/timing`, `fits/control`; skipped/superseded outputs kept under `fits/skipped-*`, `fits/superseded-*`.
- Documents: `configs/run-strike-screen-fits-matrix.json` (+ scoring `run-strike-region-scores-matrix.json`); resolved copies and the runbook in `documents/study`; summary table `scratchpad` copy of the shard skill above.
- Decisions and review records: ADR-0270 entries in `docs/architecture/decision-log.md` (23 lens reviews, every Critical/Major closed).
- Scores: pilot root `scores/strike-region-scores-matrix` (rows.parquet 1.50 GB, cells.parquet, curves.json, summary.json). Next authorized action: the owner's call (see the lane note).
