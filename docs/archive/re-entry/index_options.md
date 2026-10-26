## Archived 2026-10-26 — v11 completion and Fable handoff

# RE-ENTRY — index_options

Updated: 2026-10-25 by GPT-6. Previous note: docs/archive/re-entry/index_options.md.

## Now

- Prescription is canonical ADR-0278. Its historical ADR-0276 labels remain in sealed artifacts; upstream prediction is ADR-0276, proposed tuning ADR-0277.
- Session memo: children/index_options/docs/memos/2026-10-25-prescription-session-results.md. It covers condors, caps, free integer books, funded runs, failures, next steps and all 17 fills from the best completed account run.
- Completed funded 28–32DTE: M05 rho .03 net $6,800.05, stress $403.80, final $41,800.05 on $35,000 contributed; TWR13.44%, max drawdown65.88%. Four of ten older methods completed; others unscored. 2019 base-MLP fallback, same-year screen; no forward-performance claim.
- Latest v11 full-DTE run is active: 17 pre-2019 screened tickers, supported positive DTE≤60, M05 rho .03, $10k plus $1k every14days, weekly decisions. One profit solve, $50 absolute gap, relative0,900seconds; unresolved native timeouts skip new orders and continue existing positions/deposits.
- Code checkpoint82ac1ee1; activation lock565e6a28c22ce307559d1b30b8361f388b0fa0295b6fa06ce7b6da5eb0aca61b.123 focused tests and13 embedded families passed. Both fresh final lenses passed C0/M0/new Minor0/Nit0.
- Runtime/report: /home/russell/.cursor/projects/home-russell-dskit/canvases/index-options-2019-condor-oracle.canvas.tsx. Worktree: /home/russell/wt/index-options-harvest-20261022. No source/artifact edits while service active.
- Prediction leaders remain M51 +0.68435% and M53 +0.69034% twCRPS versus recalibrated market, positive all five regimes; M57/M64/M65 completed upstream and did not improve the leaders. See round-five memo/addendum.

## Next

1. Monitor index-options-2019-full-dte-v11.service, launched 2026-10-26 03:22:52 UTC; journal confirms initialization and START M05 W1 .03. PID2713844, invocation bc5e40558c1844f08a8097651e6d1ebd. No duplicate execution.
2. When the service ends, verify raw completion plus independent audit; append results, timeout counts, P&L/drawdown/stress and full trade history to the session memo; wrap that result addendum. Hourly thread follow-up finish-2019-options-run-memo is active; pause it after final delivery.
3. Review quote artifacts, near-zero debit/high-count books, boxes, fill shortfalls and payoff concentration before more 2019 tuning. New universe/DTE/count/timeout policies prevent attribution to DTE alone.
4. Predictive packet six under proposed ADR-0277 remains separate; no new M66–M70 work here.

## Blocked / owner decisions

- Owner approved build/ADRs, offline2019 execution, trusted local native bounds, $50 gap and900-second unresolved-week skips; do not ask again.
- Saved replay independently verifies positions/caps/values/cashflow/chronology, but coherent forged native bounds are outside this approved study contract.
- Deferred minors: tuple/list cleanup compatibility, auxiliary LP thread configuration; earlier quote-spread rounding refusal remains documented. American assignment/broker margin/intraday fills are unmodeled.
- 2024–25 stays closed; owner intends eventual January2024 then month-by-month testing only after rules freeze. 2026 stays sealed.

## Read first

- Root/lane AGENTS.md and docs/skills/implementation-workflow.md.
- docs/decisioning/path.csv: locked A0001–A0008.
- docs/memos/2026-10-25-prescription-session-results.md.
- docs/memos/2026-10-21-edge-methods-round5-results.md and completion addendum.
- Canonical ADR-0278 and historical review locks in docs/architecture/decision-log.md.

## Do not

- Do not open holdout/2026, pull Theta data, modify Path/Current Work, or delete pilot data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B, numerical threads1; one heavy study worker; C:≥30GiB.
- Keep the active worktree while the background run needs it. Runtime edits invalidate its pinned identity.

## Replaced at prescription wrap, 2026-10-25

# RE-ENTRY - index_options

Updated: 2026-10-22 by GPT-6. Previous notes in docs/archive/re-entry/index_options.md.

## Now

- ADR-0276 is the canonical strike-screen ID (initially ADR-0270; the statistical-arbitrage ADR-0270 keeps that number). Latest evidence: children/index_options/docs/memos/2026-10-21-edge-methods-round5-results.md, including the 2026-10-22 completion addendum. Prediction only.
- Path A0002/A0003/A0005/A0007/A0008 stays locked. Ranked validation is 2020-23; 2019 warm-up is unranked; the 2024-25 holdout and 2026 remain sealed.
- M51 (90-input MLP 64, quantile average by market-dispersion bucket) scores +0.68435% twCRPS versus the recalibrated market, gate 233/831 versus 35.72 expected luck. M53 (plus 21-day refits) scores +0.69034%, gate 200/831. Both positive in all five regimes.
- Completed after disk relief: M57 bag blend +0.58167%, M64 66-input blend +0.54946%, M65 106-input blend +0.57846%; gates 245/241/236 of 831. Standalone 66/106-input fits +0.11294/+0.23925%, one seed each. No new blend beats M51/M53.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018; packet 5 wall 9,554/14,400 s; scores/ 37,968,358,276 of 40,000,000,000 bytes; C: 113 GiB free after completion. Existing comparison CSVs preserve historical rows from offloaded folders.

## Next

1. Owner approval of proposed ADR-0277, then register five JSON-only tuning variants M66-M70 around M51/M53, run focused validation and A0007 analyses under the 50 GiB C: guard and 40 GB evidence cap.
2. Keep M51/M53 as leaders unless a variant meets ADR-0277's pre-registered comparison rule. Seek a separate owner decision before the 2024-25 holdout; 2026 stays sealed.
3. Later: recalibrated market inside the scorer and a serving document for the chosen four-CDF weight table.

## Blocked / owner decisions

- ADR-0277 is proposed. Root AGENTS.md requires ADR approval and permission before new config files. No packet-6 work starts until approval.
- The known pipeline purity failure predates this lane (dskit.onboarding.snapshot import in parquet.py); no code in that area changed.

## Read first

- Root and lane AGENTS.md; children/index_options/docs/decisioning/path.csv; the round-5 memo and its completion addendum; ADR-0276/0277 in docs/architecture/decision-log.md.

## Do not

- Do not read 2026 records or the 2024-25 holdout, claim trading/P&L, pull Theta data, change owner-only Path/Current Work, or delete pilot data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B, PYTHONDONTWRITEBYTECODE=1, CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA; one heavy process at a time.


## Archived 2026-10-22 (replaced after the round-5 completion)

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

---

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

---

## Archived 2026-10-21 (replaced by the round-5 wrap note)

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
