# RE-ENTRY - index_options

Updated: 2026-10-20 by Claude Sonnet 5.5 / Fable 5.1. Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen built and run twice. Latest result: children/index_options/docs/memos/2026-10-20-walkforward-results.md (long CSVs in docs/memos/data/2026-10-20-walkforward/). Older stale-model screen: memos/2026-10-19-strike-screen-matrix-results.md.
- Path (locked): A0001 approach, A0002 evaluation (twCRPS weighted |z| 0.5-2.5, regions pooled only), A0003 windows, A0004 fold rungs (screen rung superseded), A0005 promotion rule, A0006 cell = (ticker, expiry rank), A0007 simple gate, A0008 yearly retraining, TCN chain arm, seed 11.
- Walk-forward result (TCN, chain arm, 5 yearly retrains, 50+ epochs each): beats the per-name baseline every year (+0.2..2.7% twCRPS) and ties the pooled empirical (+0.4%), but loses to the raw market overall (-0.7%, t -2.2): wins 2021 (+2.2%), loses the 2022 rate shock (-4.4%) and the 2020 crash (-2.5%). Short expiries lose (rank 1 -2.1%), ranks 5-8 tie or edge (+0.1..+1.0%).
- Training: validation loss bottoms in epoch 1-2 and rises for 50 epochs while train loss falls; lr 1e-4 moves the best epoch to 4-9 with the same results. Not an epoch-count problem.
- Gate: 75 of 831 (ticker, rank) cells pass, 76 expected by luck; no signal beyond chance. Leads only: AVGO ranks 5-7, KKR 7, DE 5-6, AAPL 7, CMG 7, AMAT 5-6, COF 6.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018 (not in git): fits/wf-tcn*, scores/strike-region-scores-wf-tcn*, analysis/, prepare/walkforward/ (run scripts, plan). Packet 2 used 4,927 of 14,400 s (registration/wall-used-p2.txt).
- Protocol configs/strike-screen-protocol.json revision 5. R5 consumed, R6 packet never launched.

## Next

1. Build market_calibrated (PIT-recalibrated market, ADR-0269) and score the same runs against it; it is the production comparator and will be harder to beat.
2. Fix the overfit and the regime flip: shared head, stronger regularization, early stop at epoch 1-2, regime inputs; add the missing design pieces (13 logit-difference chain vector, vol-unit returns, optional probit-PIT residual arm). Add seed 29 for noise floor.
3. Only for cells that beat the calibrated market and the baselines: holdout conversion (end_before 2026), one confirmation run, registration, serving.

## Blocked / owner decisions

- Owner: build market_calibrated next or stop. Assumptions to confirm: fit outputs counted as evidence-class (4 GB); RSS breaches (panel build waived in writing; queues peaked 8-13 GiB).
- git push failed on GitHub credentials during the run (no Username); if still failing the owner runs `git push`.
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in ParquetStreamRows, on main).

## Read first

- CLAUDE.md; docs/decisioning/path.csv (locked rows bind: cite the id); the walk-forward memo; the last ADR-0270 entries of docs/architecture/decision-log.md.
- docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md (design) and the 2026-10-18 records.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits; one heavy process at a time (host memory guard kills at about 14 GiB).
- Owner-only Path/Current Work (edit only with explicit owner permission); R5 and the R6 packet untouched.
