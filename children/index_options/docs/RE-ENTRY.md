# RE-ENTRY - index_options

Updated: 2026-10-19 by Claude Fable 5.1 / Sonnet 5.5. Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike-region fast screen: pipeline built and run on 118 names, 2017-2023 (holdout 2024-2025 sealed, 2026 sealed forever). Memo: children/index_options/docs/memos/2026-10-19-strike-screen-matrix-results.md.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018 (not in git): converter, onboarding, 1.21M-row panel (sha 186def50), resolved documents, 20 matrix cells (fits/matrix), L1/L3 baseline fits, runbook documents/study/README.md.
- Protocol configs/strike-screen-protocol.json revision 5 (sha 073d39cb). R5 stays consumed; R6 packet never launched.
- Matrix result (model vs per-name empirical baseline, weighted CRPS skill): fold 1 +0.1..+0.9%, fold 2 +1.2..+2.2%; best context_chain/tcn; chain arm > context-only; long DTE best. Every cell overfits by epoch 1-2.
- NOT done: StrikeRegionScores over the matrix (market-implied comparison, listed-strike Brier, regions, promising cells). Killed by the host memory guard on a fold-2 cell (706k validation rows).
- Speed-ups landed (bit-identical): CUDA-graph step, lean panel load, vectorized raw-chain prepare, nearest-horizon baseline rule.

## Next

1. Rerun scoring with a memory sampler and nothing else running (group scoring-matrix; wall left about 3,100 s of the 14,400 s packet), or reduce the per-cell validation band; then read the market comparison and name the cells that beat it.
2. Build the design's missing pieces: vol-unit sequence, 13 logit-difference chain vector, shared head, optional probit-PIT residual arm; rerun the matrix.
3. Only for cells that beat the market: holdout conversion (end_before 2026), one confirmation run, then registration and serving.

## Blocked / owner decisions

- Scoring rerun is the owner's call (host memory). Assumptions to confirm: fit outputs counted as evidence-class (4 GB), the 8 GiB RSS breaches waived (panel build only was waived in writing).
- Merge to main withheld: the market comparison is unfinished.
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in ParquetStreamRows, on main).

## Read first

- CLAUDE.md; the last ADR-0270 entries of docs/architecture/decision-log.md; the memo above.
- docs/research/distribution-modeling/2026-10-18-*.md (the design); pilot root documents/study/README.md.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits.
- Owner-only Path/Current Work; R5 and the R6 packet untouched.
