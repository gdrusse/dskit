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

---

## Archived 2026-10-21 (replaced by the round-4 note)

# RE-ENTRY - index_options

Updated: 2026-10-20 by Claude Fable 5.1 (earlier work Opus 5.5, Sonnet 5.5). Previous note in docs/archive/re-entry/index_options.md.

## Now

- ADR-0270 strike screen, predictability only. Latest: children/index_options/docs/memos/2026-10-20-edge-methods-round3-results.md (39 methods, six fitted variants, training diagnosis, Theta Greeks check; CSVs in docs/memos/data/2026-10-20-edge-methods/). Before: round2-results.md, edge-methods-results.md (round 1), adjustments-results.md (R1/R2), walkforward-results.md.
- Path (locked): A0001-A0008 (twCRPS score, (ticker, expiry rank) cell, simple gate, yearly retraining).
- Best family: quantile average of model + recalibrated market + pooled + per-name baseline, weights learned on earlier years. M29 (weights per market-dispersion bucket) +0.59% twCRPS over the recalibrated market (t 9.4); M26 (21-day refits per DTE week) +0.58%; M33 (eight-variant model bag in the model slot) +0.56%, gate 241/831 vs 28 luck; M16 was +0.55%. Seven methods positive in all five regimes. Edge peaks at 43-49 DTE and in the right shoulder/wing; 2022 beyond 4 weeks still loses.
- Standalone models: none beats the recalibrated market (MLP +0.03%, R1 TCN -0.13%, GRU -0.32%, wide 90-input -0.26%); best epoch is 1-2 in 22 of 36 fold fits; the limit is the inputs, not capacity or optimizer. Inside the stack every model gives the same +0.55%.
- Theta Greeks/volume/OI/depth/own IV: 24 panel inputs omitted by the 66-input arm were tried as model inputs (M36) and as conditioning features (M37 -1.26%, M38 -0.99%, M39 +0.35% vs M34 +0.54%). No added information. The node reads panel columns for any kind via feature_source.
- Node dskit/pipeline/libs/cdf_grid_methods.py (GridCDFMethod: pit_map, linear_pool, best_component, binary_gbm, beta_pool, quantile_average, location_scale; rolling refits, level bands, feature weights, external components and feature_source); documents configs/run-edge-method-01..39-*.json (+31a, 33a); analysis_wf.py --method-rows --candidate for the gate.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018: fits/wf-{r1,r1s29,gru,mlp,mix5,l3,s47,wide}, scores/strike-region-scores-wf-*, scores/edge-method-*, analysis/. Refused warm-up folds: seed 29, seed 47, GRU 1 and 3, wide 3 (monitor still improving at 150 epochs). Packets: p3 13,488/14,400 s, p4 2,708 s. Evidence 22 of 24 GB (owner cap).

## Next

1. Combined recipe: quantile average, weights per dispersion bucket, refit every 21 days, MLP or the eight-variant bag in the model slot. Candidate for the holdout.
2. Holdout 2024-25 once, opened by the owner, for that recipe and M29/M33.
3. Recalibrated market inside the scorer; a serving document (four CDFs + weight table + refit schedule).
4. Owner decision: raise the warm-up-fold ceiling (about one packet) so every variant has 2019 rows.

## Blocked / owner decisions

- git push fails from this session (GitHub credentials unreachable). Owner runs: git push origin claude/strike-fast-scan-20261018 && git push origin HEAD:main.
- tests/pipeline purity failure predates the lane (dskit.onboarding.snapshot in parquet.py, on main since 2026-10-08).

## Read first

- CLAUDE.md; docs/decisioning/path.csv (locked rows bind: cite the id); the round-3 memo; the last ADR-0270 entries of docs/architecture/decision-log.md.
- docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md (design) and the 2026-10-18 records.

## Do not

- Never read 2026 market records or the 2024-2025 holdout before the owner opens it; no profitability claim.
- Never write options-* stores, start/stop Theta pulls, call 25503, or delete under /home/russell/data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B; PYTHONDONTWRITEBYTECODE=1; CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA fits; one heavy process at a time (host memory guard kills at about 14 GiB).
- Owner-only Path/Current Work (edit only with explicit owner permission); R5 and the R6 packet untouched; no new architecture or input work without an owner ask.
- Every document that runs (fit, scoring, method) is saved in children/index_options/configs/ in the committed placeholder form and registered in tests/test_configs.py; the pilot root documents/study/ holds only the resolved copies (owner rule 2026-10-20).
