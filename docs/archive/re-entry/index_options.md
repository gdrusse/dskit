# RE-ENTRY - index_options

Updated: 2026-10-20 by Claude Sonnet 5.5 (earlier work Fable 5.1). Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen: three result sets. Latest: children/index_options/docs/memos/2026-10-20-adjustments-results.md (CSVs in docs/memos/data/2026-10-20-adjustments/). First walk-forward: memos/2026-10-20-walkforward-results.md. Stale-model screen: memos/2026-10-19-strike-screen-matrix-results.md.
- Path (locked): A0001 approach, A0002 evaluation (twCRPS weighted |z| 0.5-2.5, regions pooled only), A0003 windows, A0004 fold rungs (screen rung superseded), A0005 promotion rule, A0006 cell = (ticker, expiry rank), A0007 simple gate, A0008 yearly retraining, TCN chain arm, seed 11.
- Adjustments worked: validation 2020-23 twCRPS edge vs raw market -0.67% (main) to -0.01% (R1: shared head, wd 0.3, dropout 0.2) and +0.11% (R2: R1 + vol-unit returns, 13 logit differences, regime scalars); vs recalibrated market -0.78 / -0.13 / -0.01; vs per-name baseline +1.3 / +1.9 / +2.0; vs pooled +0.4 / +1.0 / +1.1. A tie with the market, not a win. In-sample gap shrank 4.9 to 2.1 points.
- Gate vs recalibrated market: 93 (main), 120 (R1), 94 (R2) of 831 cells pass; luck 80 / 69 / 64; 67 cells pass in R1 and R2, 12 in all three. Leads: EOG ranks 4-7, KR 4-7, APA, DAL, BMY 7, AKAM 1, DIS 1. Earlier leads (AVGO, KKR, CMG) did not repeat: one seed.
- Training: R1/R2 train loss barely falls; best epoch 1 (R2 all folds), R1 20-23 in two folds. R2 fold 1 (2019 warm-up, unranked) skipped: patience 50 not exhausted in 150 epochs.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018 (not in git): fits/wf-*, scores/strike-region-scores-wf-*, panel-v2/, analysis/, prepare/walkforward/ (wf_run.sh, wf_prep3.sh, make_panel_v2.py). Packet 2 used about 9,760 of 14,400 s; scores/ evidence cap raised to 8 GB (5.7 GB, then 7.3 GB used).
- Protocol configs/strike-screen-protocol.json revision 5. R5 consumed, R6 packet never launched.

## Next

1. Second seed (29) on R1 and R2 for the noise floor; then the residual (probit-PIT) arm.
2. Build market_calibrated inside the scorer (analysis stage has it now) and make it the gate market.
3. Only for cells that beat the recalibrated market and baselines across seeds: holdout conversion (end_before 2026), one confirmation run, registration, serving.

## Blocked / owner decisions

- git push fails from this session (GitHub credentials; WSL-Windows interop down). Owner runs: git push origin claude/strike-fast-scan-20261018 && git push origin HEAD:main from a working shell.
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in ParquetStreamRows, on main).

## Read first

- CLAUDE.md; docs/decisioning/path.csv (locked rows bind: cite the id); the adjustments memo; the last ADR-0270 entries of docs/architecture/decision-log.md.
- docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md (design) and the 2026-10-18 records.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits; one heavy process at a time (host memory guard kills at about 14 GiB).
- Owner-only Path/Current Work (edit only with explicit owner permission); R5 and the R6 packet untouched.
