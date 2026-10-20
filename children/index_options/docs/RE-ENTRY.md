# RE-ENTRY - index_options

Updated: 2026-10-19 by Claude Fable 5.1 / Sonnet 5.5. Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike-region fast screen: pipeline built and run on 118 names, 2017-2023 (holdout 2024-2025 sealed, 2026 sealed forever). Memo: children/index_options/docs/memos/2026-10-19-strike-screen-matrix-results.md.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018 (not in git): converter, onboarding, 1.21M-row panel (sha 186def50), resolved documents, 20 matrix cells (fits/matrix), L1/L3 baseline fits, runbook documents/study/README.md.
- Protocol configs/strike-screen-protocol.json revision 5 (sha 073d39cb). R5 stays consumed; R6 packet never launched.
- Matrix fits 20/20 (model vs per-name baseline +0.1..0.9% fold 1, +1.2..2.2% fold 2) and scoring 20/20 on a 50,000-row validation sample per cell (validation_band_sample).
- VS THE MARKET-IMPLIED CURVE: the market wins on every proper score (weighted CRPS by 0.8-3.3%); models win only region Brier in a left-tail pocket at 36-60 DTE (z -2.0..-1.0, about +2%, both folds). Models are far better calibrated than the RAW market (reliability), which a recalibrated market would erase: market_calibrated not built, so no production candidate yet.
- Per name vs the raw market (chain arm): BX and KKR beat it in both folds at t>3 (+5..7%, +7..10%); COST, APO, JPM, BLK, COF, AMAT positive in both but fading; 13 of 109 names win both folds, about chance.
- Speed-ups landed (bit-identical): CUDA-graph step, lean panel load, vectorized raw-chain prepare, nearest-horizon baseline rule.

## Next

1. Build market_calibrated (PIT-recalibrated market, ADR-0269) and score the matrix against it; this is the production comparator.
2. Build the design's missing pieces: vol-unit sequence, 13 logit-difference chain vector, shared head, probit-PIT residual arm (model corrects the market curve); rerun the matrix; fix the overfit (finer early stopping, wd 0.3, shared head).
3. Only for cells that still beat the calibrated market (candidate: left tail, 36-60 DTE): holdout conversion (end_before 2026), one confirmation run, registration, serving.

## Blocked / owner decisions

- Owner call: build market_calibrated and the residual arm next, or stop. Assumptions to confirm: fit outputs counted as evidence-class (4 GB), the 8 GiB RSS breaches waived (panel build only was waived in writing).
- Merge to main: branch pushed, merge awaits the owner (the deliverable is complete, no production candidate).
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in ParquetStreamRows, on main).

## Read first

- CLAUDE.md; the last ADR-0270 entries of docs/architecture/decision-log.md; the memo above.
- docs/research/distribution-modeling/2026-10-18-*.md (the design); pilot root documents/study/README.md.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits.
- Owner-only Path/Current Work; R5 and the R6 packet untouched.
