## TL;DR

Fifteen methods built on the patterns of ADR-0272/0273/0274 were registered (ADR-0275), tuned on 2020 and validated once on 2021–2025 against the new best baseline, the ADR-0274 **vol-scaled** model. On the primary next-session label **four method-cell combinations meet the pre-registered rule, all on the ATM spread**: the **two-stage residual** model (ATM 5: +0.091 → +0.110; ATM 20: +0.246 → +0.274), the **term curve** (14/30/90-day slopes; ATM 5: +0.091 → +0.105, five of five folds), and the **pooled-scaled blend** (ATM 20: +0.246 → +0.267, four of five folds, and the only method that lifts the weak 2021–2022 folds). The new vendor-Greek, smile, flow, carry, Cboe-index and market-regime features all lose to the baseline. Skew remains unforecastable. **Caveat on strength:** the pre-registered family of 32 includes 17 versus-mean tests that all reject, which loosens the BH cut; with BH over only the fifteen versus-`scaled` comparisons, only the blend and the pooled-scaled model survive (ATM 20), and the two-stage and term-curve passes do not. Prediction only: no trade rule, P&L or edge claim.

## Execution contract

- **Authority:** ADR-0275 (proposed 2026-10-21, written before code under the owner's request to analyze the patterns and register fifteen new methods) with Amendments 1 and 2. Next-session label (`label_offset` 1), folds, weekly refit clock (7-day blocks from 2018-12-31, 52 warm-up blocks), scorer and drop rules as ADR-0272. Scored on the three phase-one pairs.
- **Authorship:** design, nodes, documents and all runs were done by a Claude Fable 5.1 subagent, which hit an API session limit before writing up; this memo, the lane note, the journal row and the commit were completed by Claude Opus 5.5 from the agent's artifacts, scratch notes and ADR text. No run was repeated or altered in the hand-over.
- **Baselines in every cell:** `mean`; the frozen `lagged` (raw Ridge, alpha 100 ATM / 0.1 skew); and `scaled` (ADR-0274: `StandardizedEstimator(Ridge)` alpha 100 on `y_h / sd60`, back-transformed), the comparator.
- **"Improves"** (pre-registered): pooled 2021–2025 R²oos above `scaled`, BH-rejected Diebold–Mariano versus `scaled` (family of 32 per cell: fifteen versus `scaled` plus every non-benchmark model versus `mean`), and per-fold R²oos above `scaled` in at least four of five folds.
- **Data pass** `run-tech-structural-vendor.json` (hash `ba4e01d4…e4ca`, run `tech-structural-vendor-20261021-2026-10-21-dff1763d`, the 444 pinned Theta members): surfaces at 14/30/60/90 DTE with term slope and straddle ratio; positioning 1–60 DTE with moneyness × DTE profiles of OI, volume, count, gamma and vanna; near-money (5%) vega/vanna/vomma/gamma sums; and a vendor-delta surface (vendor IV interpolated at |Δ| 0.10/0.25/0.40/0.50 per right, OI-vega-weighted vendor IV, theta/vega carry). Artifacts under `/home/russell/data/statistical_arbitrage/structural-vendor-20261021/`, pinned by digest. Cboe VIX, VXN and the single-stock VXAPL/VXAZN/VXGOG indices are read from the onboarded `cboe-index-wide` source.
- **The fifteen methods** (each `StandardizedEstimator(Ridge)` on the scaled label with `z`, `spread_change_5` plus the listed features, per pair, weekly, alpha on {10, 100, 1000} unless stated): 1 term slope; 2 leg Z; 3 term + leg Z; 4 term curve (14-minus-30 and 30→90; the 60-DTE leg was dropped before any run, Amendment 1); 5 vendor-delta smile (put25−call25, put10−put25, wing curvature); 6 vendor-versus-parity basis; 7 near-money vanna and vomma per unit vega; 8 front-gamma concentration and asymmetry around spot; 9 flow (turnover, trade count, quote-size imbalance, OTM put/call volume); 10 carry (straddle ratio, theta/vega); 11 Cboe single-stock vol-index basis; 12 market regime (VIX, VXN, their log ratio); 13 pooled-scaled across the ADR-0274 fifteen-name cohort (105 pairs, 52-week cadence); 14 blend w·m13 + (1−w)·`scaled`; 15 two-stage residual (first stage Ridge on `z`, `spread_change_5`; second-stage Ridge on the residual with term slope, leg Z, 14-day slope, vendor basis, Cboe basis).
- **Selection:** one knob per method on the 2020 fold by the ADR-0272 rule; frozen in `configs/prediction-fifteen-selection.json` and pinned by digest. Most linear methods chose alpha 1000 or 100; blend weight 0.75 (ATM) / 0.25 (skew).
- **Rounds (disclosed, Amendment 2):** round 1 (development `fb8f797f…766a`, run `…-6a194490`; validation `d42bd231…244b`, run `…-004b2a5f`) scored only about 660 rows per ATM cell because the market panel of method 12 was built on the union of VIX and VXN dates and Cboe prints VIX on 27 US holidays in 2021–2025 when VXN and the option market are closed (and VXN on 2 dates VIX does not); each holiday voided method 12's 60-session Z for 60 sessions and, through the same-rows rule, shrank every model's sample. The fix (inner-join the two index series) was made after round 1's validation was read; round 2 (development `f7ce2ac5…4e5a`, run `…-ab28a09c`; validation `b755d360…33b7`, run `…-d4c5a83f`) is the primary result. The selection re-derived from round 2 equals round 1's exactly. Round 1's thin-sample readings are reported below for transparency; they are not the result.
- **Commands** (worktree root, `PYTHONPATH=children/statistical_arbitrage:.`, `/home/russell/dskit/.venv/bin/python`): `python -m dskit.pipeline run children/statistical_arbitrage/configs/run-tech-structural-vendor.json --asof 2026-10-21`, then `run-tech-prediction-fifteen-{development,validation}.json`.

## Implementation evidence

Two generic additions and one estimator by import path: `libs/chain_greeks.py:ChainDayDeltaSurface` (vendor IV at fixed vendor deltas, OI-vega-weighted vendor IV, carry), `ChainDayPositioning` gains `profiles` (moneyness × DTE buckets) and bid/ask size totals, and `libs/sklearn.py:TwoStageResidualEstimator`. Nine new node tests and five new child-pin functions; focused suites 1,060 passed (the one failure is main's known lightgbm-extra pin); ruff clean; CLAUDE/AGENTS pair aligned; all three documents plan under a stdlib-only interpreter.

## Pattern analysis (what the earlier studies showed)

1. **The 20-session ATM cell is where signal lives.** The 5-session label is mostly mid-surface measurement noise; `lagged` scores +0.08 at 5 sessions against +0.20 at 20.
2. **Heteroskedasticity is large and tracked by the trailing spread sd.** The scaled label's gain sat in the quiet years and in AAPL–AMZN, the pair whose spread scale swings most.
3. **2022 is the fold every addition loses** (the AMZN and GOOGL splits with vendor OI resets, and a bear-market vol regime).
4. **What helped is parsimonious and structural** (term slope, leg Z, the scaled label); what failed added many columns or vol-level information to about 1,500 rows per pair (net Greek sums, Lasso, boosting, realized vol, the Kalman direct forecast).
5. **Pooling failed for a scale reason** (raw labels of different size); a scaled-label pool was the untested fix.
6. **Pairs differ:** AAPL–AMZN is the most predictable, AAPL–GOOGL the hardest.

## Validation 2021–2025, next-session label (round 2, primary)

Pooled R²oos against the expanding mean; Δ versus `scaled`; folds above `scaled`; DM t / p versus `scaled`; BH within the cell's 32-hypothesis family.

**ATM, 5 sessions** (3,192 rows; `lagged` +0.083, `scaled` +0.091, folds +0.066 / −0.002 / +0.065 / +0.131 / +0.129)

| Method | R²oos | Δ | folds | DM t / p | BH | meets rule |
|---|---:|---:|---|---:|---|---|
| 4 term curve | +0.105 | +0.015 | 5/5 | +2.16 / 0.015 | yes | **yes** |
| 15 two-stage residual | +0.110 | +0.019 | 4/5 | +2.14 / 0.016 | yes | **yes** |
| 3 term + leg Z | +0.093 | +0.002 | 2/5 | +0.30 / 0.383 | no | no |
| 1 term / 2 leg Z / 6 basis / 14 blend | +0.090 to +0.092 | ≈0 | 3–4/5 | p ≥ 0.41 | no | no |
| 13 pooled-scaled / 11 Cboe basis | +0.088 / +0.089 | −0.003 / −0.002 | 3/5 | p ≥ 0.54 | no | no |
| 10 carry / 7 convexity / 8 profile / 9 flow / 5 vendor smile / 12 market | +0.084 to +0.072 | −0.007 to −0.018 | 0–2/5 | t −1.25 to −4.40 | no | no |

**ATM, 20 sessions** (3,147 rows; `lagged` +0.205, `scaled` +0.246, folds +0.195 / +0.048 / +0.311 / +0.392 / +0.108)

| Method | R²oos | Δ | folds R²oos | above | DM t / p | BH | meets rule |
|---|---:|---:|---|---|---:|---|---|
| 15 two-stage residual | +0.274 | +0.029 | +0.222 / +0.036 / +0.346 / +0.431 / +0.142 | 4/5 | +2.21 / 0.013 | yes | **yes** |
| 14 blend (w 0.75) | +0.267 | +0.022 | +0.286 / +0.118 / +0.323 / +0.394 / +0.099 | 4/5 | +3.05 / 0.001 | yes | **yes** |
| 13 pooled-scaled | +0.271 | +0.026 | +0.308 / +0.136 / +0.325 / +0.392 / +0.093 | 3/5 | +2.75 / 0.003 | yes | no (folds) |
| 3 term + leg Z | +0.277 | +0.031 | +0.199 / +0.039 / +0.318 / +0.393 / +0.257 | 4/5 | +1.58 / 0.057 | no | no |
| 4 term curve | +0.276 | +0.030 | +0.189 / +0.051 / +0.328 / +0.394 / +0.235 | 4/5 | +1.66 / 0.048 | no | no |
| 1 term slope | +0.271 | +0.025 | | 3/5 | +1.31 / 0.096 | no | no |
| 2 leg Z | +0.255 | +0.009 | | 4/5 | +1.48 / 0.069 | no | no |
| 6 basis / 7 convexity / 8 profile / 10 carry / 11 Cboe / 9 flow / 5 vendor smile / 12 market | +0.241 to +0.176 | −0.004 to −0.069 | | 0–2/5 | t −0.75 to −3.54 | no | no |

**Skew, 5 and 20 sessions** (2,861 / 2,816 rows; `scaled` +0.005 / +0.023): no method is BH-rejected; the pooled-scaled model and blend are the only ones winning five of five folds at 20 sessions (skew 5 +0.009 / +0.007; skew 20 +0.030 / +0.026, level with `lagged` +0.030 and flow +0.030 on pooled R², DM p 0.07–0.10 for m13 and 0.012 for m14 but not surviving BH in a 32-hypothesis family).

**Round 1 (thin sample, not the result):** with about 660 rows per ATM cell the readings were noisy (ATM 20 `scaled` +0.152; blend +0.204, BH-rejected but three of five folds; the two-stage residual +0.153) and skew 20 showed a spurious two-stage gain (+0.149 against +0.119, BH-rejected) that vanishes on the full sample (+0.020 against +0.023). This is why the alignment fix mattered.

## What this does and does not say

- **How strong the passes are depends on the family.** The rule is met as pre-registered, but the 32-hypothesis family includes 17 versus-mean tests that all reject; over only the fifteen versus-`scaled` tests, BH keeps the blend (p 0.001) and the pooled-scaled model (p 0.003) at ATM 20 and drops the two-stage residual (p 0.013, 0.016) and the term curve (p 0.015). The blend is therefore the most robust result of this study.
- **The improvements follow the patterns.** Every winner is either more term-structure information (method 4, and the second stage of 15) or a better treatment of scale and sample size (the scaled-label pool and blend). The two-stage residual shrinks its additions separately from the two-feature base; method 3 (term slope and leg Z in one Ridge, a subset of the second stage's features) scores as high at ATM 20 (+0.277) with weaker DM evidence, so the gain is mostly the term information, not the two-stage form.
- **The pooled-scaled model (and the blend built on it) fixes 2021–2022**, the folds every per-pair model loses (+0.308 / +0.136 against `scaled` +0.195 / +0.048 at ATM 20), and loses 2025 (+0.093 against +0.108); blending at 0.75 keeps most of the early gain and passes four folds. This confirms the ADR-0274 diagnosis: pooling failed for scale, not signal.
- **The unused Theta fields do not help.** Vendor-delta smile, near-money convexity, gamma profiles, flow, carry, Cboe vol-index basis and the market regime all score below the baseline, several significantly. The vendor numbers are clean and correctly aggregated (the data pass and node tests verify that); they carry no extra information about the next 5–20 sessions of the ATM-IV spread beyond what the spread's own history holds.
- **Skew stays out of reach** on every clock and every method.
- The gains are modest (+0.015 to +0.029 R²oos on a base of +0.09 / +0.25), on five folds of three overlapping pairs, and the round-2 data fix was made after a validation read (the selection did not change). A single separately registered confirmation, ideally of the pooled-scaled blend (the one pass that survives BH over the fifteen baseline comparisons alone) on the ATM 20-session target, is the honest next step before relying on them. The v6 replay showed that forecastability of this spread did not convert to P&L at NBBO.

## Reproducibility and handoff

- Artifacts: `/home/russell/data/statistical_arbitrage/prediction-fifteen-20261021/{development,validation,development-r2,validation-r2}/`; round-2 validation summary SHA-256 prefixes ATM h5 `72a8ff2ed5ac`, ATM h20 `a4ce0bf831cf`, skew h5 `fa8c0c6b12af`, skew h20 `e620387362fe`. Config digests: vendor pass `d3c96a27216a…`, development `578351cfd3f6…`, validation `5de574577f40…`, selection `086c6dc3ec22…`. Every write node declares `overwrite: false`.
- Review record: candidate `7c29fc0f`, two Sonnet lenses. Correctness: zero Critical/Major; nodes reproduced from raw Parquet on three symbol-days, the two-stage residual refit over 863 blocks to 9e-16, market features recomputed from Cboe, every headline number and the selection re-derived; Minor items corrected above (the BH-family caveat, the holiday count, the unbracketed-delta counts, wording), plus a deferred code nit (deltas that round to the same percent are not refused). Tests/integration: one Major, the Amendment-2 alignment fix had no regression pin; fixed in the next candidate by pinning the identity hashes of the three scored documents and the VIX/VXN inner-join wiring (proved to catch the union mutant). Deferred backlog: unit-test boundary gaps in `ChainDayDeltaSurface`, profiles and `TwoStageResidualEstimator`; `sklearn.base.clone` of the two-stage estimator raises; `_iso`/`_number` duplicated in `chain_greeks.py`.
- Next authorized action: none. This memo authorizes nothing.
