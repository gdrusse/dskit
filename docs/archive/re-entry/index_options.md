# RE-ENTRY - index_options

Updated: 2026-10-20 by Claude Opus 5.5 (earlier work Sonnet 5.5, Fable 5.1). Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen. Latest: children/index_options/docs/memos/2026-10-20-edge-methods-results.md (ten methods, CSVs in docs/memos/data/2026-10-20-edge-methods/). Before: memos/2026-10-20-adjustments-results.md (R1/R2), memos/2026-10-20-walkforward-results.md.
- Path (locked): A0001 approach, A0002 evaluation (twCRPS, regions a decomposition), A0003 windows, A0004 fold rungs, A0005 promotion rule, A0006 cell (ticker, expiry rank), A0007 gate, A0008 yearly retraining.
- First edge over the market: M08 (model + recalibrated market + pooled empirical + per-name baseline, simplex weights per DTE week, learned on earlier years) beats the recalibrated market +0.48% twCRPS (t 8.4), raw market +0.59%; gate 215/831 vs 29 by luck. Pools M02-M06 +0.30..0.37%. Edge grows with DTE (peak 36-49 days, +0.6..1.0%), largest in the right shoulder/wing; ties 2022. Losers: model PIT map M07, LightGBM M10, per-cell switch M09.
- Node dskit/pipeline/libs/cdf_grid_methods.py (GridCDFMethod); documents configs/run-edge-method-NN-*.json over the R1 rows; analysis_wf.py --method-rows for the gate.
- R1 = shared head, wd 0.3, dropout 0.2 (ties market alone); R2 adds design inputs (fold 1 skipped).
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018: scores/edge-method-*, analysis/edge-method-*, superseded set moved to scores/superseded-edge-methods-nozones-20261020/.

## Next

1. Owner: evidence cap breach (scores/ 10.4 GB > 8 GB): raise or move superseded outputs off the evidence root.
2. Second seed (29) of R1, rerun M08/M02 on it; then the 2024-25 holdout once for M08 and M02.
3. Build the recalibrated market into the scorer; registration and serving of the stack (four CDFs + a weight table per DTE week).

## Blocked / owner decisions

- git push fails from this session (GitHub credentials; WSL-Windows interop down). Owner runs: git push origin claude/strike-fast-scan-20261018 && git push origin HEAD:main.
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in ParquetStreamRows, on main).

## Read first

- CLAUDE.md; docs/decisioning/path.csv (locked rows bind: cite the id); the adjustments memo; the last ADR-0270 entries of docs/architecture/decision-log.md.
- docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md (design) and the 2026-10-18 records.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits; one heavy process at a time (host memory guard kills at about 14 GiB).
- Owner-only Path/Current Work (edit only with explicit owner permission); R5 and the R6 packet untouched.
