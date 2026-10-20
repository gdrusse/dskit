# ADR-0270 strike screen: data build, fits and first results (2026-10-19)

## TL;DR

The Theta-to-model pipeline now runs end to end on 118 names (2017-2023): 20 designed models fit in 51 minutes and beat the per-name empirical baseline by 0.1-0.9% (2019 validation) and 1.2-2.2% (2020-2023 validation), the chain-feature arm best, longest horizons most. The comparison that matters, against the market-implied reference at the listed strikes, is NOT done: the scoring step was stopped by the host memory guard. No result here is a claim of beating the market.

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
| StrikeRegionScores over the matrix | Attempt 1 failed (baseline unseen-horizon KeyError; fixed in the scorer, `bb08f71b`); attempt 2 killed by the Claude Code host memory guard. NOT DONE. |

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

- Rows: 160,754 (fold 1) and 706,003 (fold 2) validation rows per cell; shard-level means; no uncertainty estimate yet (the scorer computes clustered standard errors).
- Fold 1: short horizons (1-21 DTE) are at or below the baseline for most cells; only 36-60 DTE is clearly positive (+1.1% to +2.0%). Fold 2: all DTE slices positive; 1-7 DTE +0.4% to +1.9%.
- The chain arm beats context-only by 0.2-0.8 points in fold 2 for every encoder; encoder choice matters less than the chain inputs.
- Every cell overfits fast: best monitor epoch 1-2, training loss falls, monitor loss rises from there (per-epoch curves are in each checkpoint `state.json`). Per-ticker heads (116) are the likeliest memorizer.

## Not done and caveats

- Market-implied comparison, listed-strike Brier, per-region (14 z regions) and per-cell promising-rule evaluation: need the scorer. The run died on host memory (peak unknown; the first fold-2 cell has 706k validation rows). Next: run the scorer with a memory sampler and nothing else on the host, or cut the validation band per cell.
- Deviations from the design (each needs code): raw returns instead of asinh(ret/rv_22); the 19 rn_q quantiles instead of the 13 logit differences logit F_Q(z_k) - logit Phi(z_k); one head per ticker instead of a shared head; no probit-PIT residual arm.
- Fit outputs (about 1.2 GB per 8 to 20 cells) were treated as evidence-class output (4 GB) not the 1 GB cap, by assumption; the 8 GiB host-RSS cap was breached in the panel build, the L1/L3 and the timing queues' scoring phase.
- Holdout conversion (2024-2025) not run; market-calibrated reference stage not run.
- Test state: lane suites green; `tests/pipeline` has one purity failure that predates this lane (`dskit.onboarding.snapshot` import in `ParquetStreamRows`, on main).

## Artifacts

- Fits: pilot root `fits/matrix` (20 cells, checkpoints, per-row score shards), `fits/L1`, `fits/L3`, `fits/timing`, `fits/control`; skipped/superseded outputs kept under `fits/skipped-*`, `fits/superseded-*`.
- Documents: `configs/run-strike-screen-fits-matrix.json` (+ scoring `run-strike-region-scores-matrix.json`); resolved copies and the runbook in `documents/study`; summary table `scratchpad` copy of the shard skill above.
- Decisions and review records: ADR-0270 entries in `docs/architecture/decision-log.md` (23 lens reviews, every Critical/Major closed).
- Next authorized action: none beyond the owner's call on the scoring rerun.
