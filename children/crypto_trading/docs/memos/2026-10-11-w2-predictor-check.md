# W2 predictor check: frozen GRU, retrained on every minute (2026-10-11)

## TL;DR

The selected model (`gru-crps_tail-pooled`, 10 features, zoo-default hyperparameters) beat both baselines on the single W2 read: weighted CRPS **2.2177** vs **2.2448** for the empirical distribution (**-1.2%**) and 2.3960 for EWMA-Student, in both retrain blocks. The gain matches W1 (-1.1%), so nothing degraded out of sample. Largest caveat: one seed, one model, three months, and the standard error is not computed for two blocks.

## Execution contract

- Pipeline: `workflow-production.json` (frozen model from `hpo-frozen.json`, winner = default; pick from `zoo-pick.json`), recipe sha256 recorded in `models.json`. Run 2026-10-11, GPU, work dir `/home/russell/data/crypto_trading/production`.
- Window: W2 only, 2026-05-02 to 2026-08-01, scored on the Binance move; no Kalshi prices, fees or MIO. W4 (from 2026-08-01) and W5 (holdout from 2026-09-15) were not read; the pipeline refuses a window past W5 and past W2 without an explicit flag.
- Schedule: block 1 trains on all history to 2026-05-01 (2,905,772 examples) and scores 2026-05-02 to 2026-07-01 (60 days, 172,800 rows); block 2 retrains on history to 2026-06-30 (3,078,572 examples) and scores 2026-07-01 to 2026-08-01 (31 days, 89,250 rows). Expanding window, frozen hyperparameters, no search.
- Training stride: **every minute** (`row_stride` 1; the zoo used every 5th). Early stopping patience 10 stopped at epoch 20 (block 1) and 24 (block 2). Scoring every minute. Seed 11.
- Saved models: every retrain is saved under `models/<lane>/block-<k>-<cutoff>/` (`model.pt`, `model.json`, `training_curve.json`) and indexed in `models.json` with per-file sha256, state hash, hyperparameter hash and `row_stride`. Baselines save their state file.

## Results (weighted CRPS, lower is better)

| Block | Days | GRU | Empirical | EWMA-Student | GRU vs empirical |
|---|---|---|---|---|---|
| 1 (from 2026-05-02) | 60 | 2.2204 | 2.2478 | 2.4232 | -0.0274 (-1.22%) |
| 2 (from 2026-07-01) | 31 | 2.2123 | 2.2390 | 2.3434 | -0.0267 (-1.19%) |
| all (262,050 rows) | 91 | 2.2177 | 2.2448 | 2.3960 | -0.0271 (-1.21%) |

Other metrics, block 1 (GRU vs empirical): CRPS 1.5402 vs 1.5571, tail CRPS 0.6802 vs 0.6906, pinball 0.6388 vs 0.6473, PIT KS 0.0162 vs 0.0274. Block 2: CRPS 1.5332 vs 1.5497, tail CRPS 0.6791 vs 0.6893, pinball 0.6353 vs 0.6435, PIT KS 0.0148 vs 0.0285. Calibration is better than the baseline but PIT p-values still reject exact uniformity (1e-19 and 4e-8), as in W1.

## Caveats

- This is the one W2 read. Any re-read or change after seeing it is a new decision (plan rules).
- HPO found nothing (the default won), so the W2 model is the zoo default retrained on every minute; the stride change (5 to 1) and the retrain are not separable here, but W1 and W2 gains agree.
- Two blocks of different lengths; no standard error is reported. One seed.
- The feature selection rule (ADR-0258), tail wings (provisional), BVOL `missing: mean` and the W4 "already scored" question remain open owner decisions.

## Handoff

Next authorized step: none requiring W4/W5. The frozen model files per retrain block are the artifact for MIO work at W4; W4 and W5 remain owner-gated. A wider hyperparameter search on the warmup is optional and would come before any further W2 use only as a new decision.
