# RE-ENTRY - index_options

Updated: 2026-10-21 by Claude Fable 5.1 (earlier work Opus 5.5, Sonnet 5.5). Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen, predictability only. Latest: children/index_options/docs/memos/2026-10-20-edge-methods-round4-results.md (M40-M50: Greeks/wide inputs with other architectures; CSVs in docs/memos/data/2026-10-20-edge-methods/ cover M01-M50). Before: round3-results.md, round2-results.md, edge-methods-results.md (round 1), adjustments-results.md (R1/R2), walkforward-results.md.
- Path (locked): A0001-A0008 (twCRPS score, (ticker, expiry rank) cell, simple gate, yearly retraining).
- Best family: quantile average of model + recalibrated market + pooled + per-name baseline, weights learned on earlier years. New best M48 (wide MLP 64 in the model slot, weights per DTE week) +0.64% twCRPS over the recalibrated market (t 11.1), positive in all five regimes incl. 2022 +0.32%, gate 252/831 vs 31 luck, 21 tickers positive all four years; the 2022 long-DTE loss is gone. M47 (wide MLP 32) +0.60%; M29 (dispersion buckets) +0.59%; M26 (21-day refits) +0.58%; M33/M50 (model bags) +0.56%. Edge peaks at 43-49 DTE (M48 +1.20%) and in the right shoulder/wing.
- Standalone models: the wide MLP 32 (90 inputs) is the first above the recalibrated market, +0.18%; wide MLP 64 +0.12% (at par in 2022); 66-input MLP +0.03%; every TCN/GRU below (R1 -0.13%, wide TCN 16 -0.26%, wide TCN 32 -0.10%). The wide inputs help an MLP, not a TCN. Best epoch still 1-2 in most folds; standalone gates at luck.
- Theta Greeks/volume/OI/depth/own IV as weight features (M39-M42, M46: +0.32% to +0.53%) stay below M34's three market-shape features (+0.54%); as post-hoc corrections they lose (M37, M38, M43 on the M29 stack -0.31%, M44 -1.39%, M45 -1.27%). Stop that line. The node reads panel columns for any kind via feature_source.
- Node dskit/pipeline/libs/cdf_grid_methods.py (GridCDFMethod: pit_map, linear_pool, best_component, binary_gbm, beta_pool, quantile_average, location_scale; rolling refits, level bands, feature weights, external components and feature_source); documents configs/run-edge-method-01..50-*.json (+31a, 33a, 50a) and configs/run-strike-screen-fits-wf-*.json / run-strike-region-scores-wf-*.json (placeholder form); analysis_wf.py --method-rows --candidate for the gate.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018: fits/wf-{r1,r1s29,gru,mlp,mix5,l3,s47,wide,wide-mlp,wide-mlp64,wide-tcn32}, scores/strike-region-scores-wf-*, scores/edge-method-*, analysis/ (round-4 analyses under the short name edge-method-NN), superseded/. Refused folds: seed 29, seed 47, GRU 1 and 3, wide 3, wide TCN 32 fold 1. Packets: p3 13,488/14,400 s, p4 9,838/14,400 s. Evidence 29.3 of 32 GB (owner cap, raised 2026-10-20).

## Next

1. Combined recipe: quantile average, weights per dispersion bucket (M29), refit every 21 days (M26), wide MLP 64 in the model slot. Candidate for the holdout with M48 and M29.
2. Seeds 29 and 47 of the wide MLP 64, and a hidden-64 dropout-0.3 fit on the 66 inputs (three fits) to pin the input effect. Then the holdout 2024-25 once, opened by the owner.
3. Recalibrated market inside the scorer; a serving document (four CDFs + weight table + refit schedule).
4. Owner decision: raise the warm-up-fold ceiling (about one packet) so every variant has 2019 rows.

## Blocked / owner decisions

- Branch pushes work from this session; the push to main is refused by the session policy. Owner runs: git push origin HEAD:main.
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in parquet.py, on main since 2026-10-08).

## Read first

- CLAUDE.md; docs/decisioning/path.csv (locked rows bind: cite the id); the round-4 memo (then round 3); the last ADR-0270 entries of docs/architecture/decision-log.md.
- docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md (design) and the 2026-10-18 records.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits; one heavy process at a time (host memory guard kills at about 14 GiB).
- Owner-only Path/Current Work (edit only with explicit owner permission); R5 and the R6 packet untouched; no new architecture or input work without an owner ask.
- Every document that runs (fit, scoring, method) is saved in children/index_options/configs/ in the committed placeholder form and registered in tests/test_configs.py; the pilot root documents/study/ holds only the resolved copies (owner rule 2026-10-20).
