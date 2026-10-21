# RE-ENTRY - index_options

Updated: 2026-10-21 (wrap) by Claude Fable 5.1 (earlier work Opus 5.5, Sonnet 5.5). Previous notes in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen, predictability only, wrapped after round 5. Latest: children/index_options/docs/memos/2026-10-21-edge-methods-round5-results.md (M51-M63, seeds, patterns across rounds; CSVs in docs/memos/data/2026-10-20-edge-methods/ cover M01-M63 with ticker x DTE x zone tables). Before: round4-results.md (M40-M50), round3, round2, edge-methods-results.md (round 1), adjustments-results.md, walkforward-results.md.
- Path (locked): A0001-A0008 (twCRPS score, (ticker, expiry rank) cell, simple gate, yearly retraining).
- Best family: quantile average of model + recalibrated market + pooled + per-name baseline, weights learned on earlier years, on the wide MLP 64 (90 Theta chain inputs, hidden 64, dropout 0.3). M53 (weights per market-dispersion bucket, refit every 21 days) +0.69% twCRPS over the recalibrated market (t 9.8); M51 (dispersion buckets only) +0.68% (t 10.7), gate 233/831 vs 36 luck, 91 of 110 tickers positive, 21 all four years; M48 (per DTE week) +0.64%. Positive in all five regimes, 2022 +0.40%. Edge peaks at 43-49 DTE and in the right shoulder/wing; only 50-60 DTE in 2022-23 still loses.
- Standalone fits: wide MLP 64 seed 29 +0.20% over the recalibrated market, seed 11 +0.12%, seed 47 -0.02%; the three-seed bag alone +0.29%. Seed spread 0.22% is the largest uncontrolled effect: compare fits with three seeds or a bag. TCNs do not profit from the wide inputs; Greeks as weight features or post-hoc corrections never helped (rounds 3-5).
- Not run (C: drive at the owner's 30 GiB floor; documents committed and validated): M57 (recipe on the seed bag), fits mlp64-d03 (66 inputs) and wider-mlp64 (106 inputs) with M64/M65.
- Node dskit/pipeline/libs/cdf_grid_methods.py (GridCDFMethod); documents configs/run-edge-method-01..65-*.json (+31a, 33a, 50a, 57a) and configs/run-strike-screen-fits-wf-*.json / run-strike-region-scores-wf-*.json (placeholder form, resolved copies in the pilot root documents/study/); analysis_wf.py --method-rows --candidate for the gate; collect.py and summ*.py (session scratchpad) build the memo tables.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018 (49 GB): fits/wf-*, scores/strike-region-scores-wf-*, scores/edge-method-*, analysis/, superseded/ (3.1 GB of replaced outputs). Packets: p3 13,488, p4 9,838, p5 4,454 of 14,400 s. Evidence 34.4 of 40 GB (owner cap). Host C: 30 GiB free: nothing more runs until the owner frees space.

## Next

1. Owner frees C: space, then run M57, the two fits and M64/M65 (one packet).
2. Holdout 2024-25 once, opened by the owner, for M51 and M53 (lineage M48, M29), with the seed bag in the model slot if M57 confirms.
3. Recalibrated market inside the scorer; a serving document (four CDFs + weight table per dispersion bucket + 21-day refit schedule).
4. ADRs before any of: shrunk per-ticker term in the weight law, buckets on panel columns, strike-node surface as a sequence input, scorer parallelization.

## Blocked / owner decisions

- C: drive at the 30 GiB floor (WSL vhdx grows with evidence; deleting inside WSL does not shrink it). Purge candidates listed in the wrap message; nothing under /home/russell/data is deleted without the owner.
- Push to main is refused by the session policy; owner runs: git push origin HEAD:main (branch claude/strike-fast-scan-20261018 is pushed).
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in parquet.py, on main since 2026-10-08).

## Read first

- CLAUDE.md; docs/decisioning/path.csv (locked rows bind: cite the id); the round-5 memo, then round 4; the last ADR-0270 entries of docs/architecture/decision-log.md.
- docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md (design) and the 2026-10-18 records.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits; one heavy process at a time (host memory guard kills at about 14 GiB); keep C: at or above 30 GiB free.
- Owner-only Path/Current Work; R5 and the R6 packet untouched; no new architecture or input work without an owner ask.
- Every document that runs (fit, scoring, method) is saved in children/index_options/configs/ in the committed placeholder form and registered in tests/test_configs.py (owner rule 2026-10-20).
