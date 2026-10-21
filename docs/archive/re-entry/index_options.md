# RE-ENTRY - index_options

Updated: 2026-10-20 by Claude Opus 5.5 (earlier work Sonnet 5.5, Fable 5.1). Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen. Latest: children/index_options/docs/memos/2026-10-20-edge-methods-round2-results.md (20 methods; CSVs in docs/memos/data/2026-10-20-edge-methods/). Before: edge-methods-results.md (round 1), adjustments-results.md (R1/R2), walkforward-results.md.
- Path (locked): A0001-A0008 (twCRPS score, (ticker, expiry rank) cell, simple gate, yearly retraining).
- Best: M16 quantile average of model + recalibrated market + pooled + per-name baseline, weights per DTE week, learned on earlier years: vs recalibrated market +0.55% twCRPS (t 9.6), raw market +0.66%, per-name baseline +2.6%; positive in all five regimes; gate 238/831 vs 28 luck. Rolling 21-day stacks M11/M12 +0.50%, 2022 positive. Seed 29 reproduces the stack (M20). Edge largest at 36-49 DTE and in the right tail; 2022 beyond 4 weeks still loses.
- Node dskit/pipeline/libs/cdf_grid_methods.py (GridCDFMethod: pit_map, linear_pool, best_component, binary_gbm, beta_pool, quantile_average; rolling refits, recency weights, external components); documents configs/run-edge-method-01..20-*.json; analysis_wf.py --method-rows for the gate.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018: scores/edge-method-*, analysis/edge-method-*, fits/wf-r1s29 (seed 29; fold 1 skipped). Evidence 14.9 of 16 GB (owner raised the cap); superseded sets moved under scores/superseded-*.

## Next

1. Rolling quantile average per DTE week (M16 + M12); a regime input for long-DTE weights in rate shocks; raise the warm-up fold ceiling so seed 29 / R2 have 2019.
2. Holdout 2024-25 once for M16, rolling M16, M08 (owner opens it).
3. Recalibrated market inside the scorer; a serving document for the stack (four CDFs + weight table per DTE week).

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
