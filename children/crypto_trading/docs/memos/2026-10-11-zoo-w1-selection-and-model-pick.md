# W1 feature selection, model zoo and model pick (2026-10-10 to 2026-10-11)

## TL;DR

The W1 zoo picked **`gru-crps_tail-pooled`** (a GRU trained on CRPS plus tail CRPS, one model for BTC and ETH) on **10 selected features**. It beats the best baseline (the vol-standardized empirical distribution) by **1.1%** weighted CRPS, in all 14 of 14 folds. The five architectures sit within 0.5% of each other, so the pick is a narrow lead, not a clear winner. Largest caveat: one seed, every 5th minute for training, and a standard error that is too small (folds share training data).

## Execution contract

- Window: W1 only (2023-06-20 to 2026-05-01), 14 expanding walk-forward folds (first 2024-01-01, 60-day steps and validation, 1-day embargo). W2, W4 and W5 were not read.
- Training: every 5th minute (`row_stride` 5, fit band only), scoring every minute; seed 11 (one seed); up to 500 epochs, early-stopping patience 10; GPU. Gaussian mixture heads, 3 components, 15 horizons (1 to 15 minutes), vol-standardized log-return target.
- Speed work (same computation, not bit-identical): the loss is `torch.compile`d for the Gaussian family and several host syncs were removed. Old versus new code agree to about 1e-5 relative on CRPS, tail CRPS and pinball.
- Selection: tabular (MLP) and sequence (other four) classes each ran 17 ablation arms x 14 folds; keep rule z = 1 standard error of the paired per-fold difference (ADR-0261 rule, **not yet owner-approved**).
- Zoo: 5 architectures x 4 losses x 3 scopes = 60 trained lanes (pooled, BTC-only, ETH-only), plus 6 baseline lanes. All 66 lanes finished OK (no failures).
- Data: batch roots under `/home/russell/data/crypto_trading/` (`batch-select-tabular-v3`, `batch-select-sequence-v3`, `batch-zoo-w1`); pick and selection in `chain/pick/` and `chain/runs/select-features-2026-05-01-15b8e3ca/`. Recipe sha256 `307558dc…ac0597`. Code: branch `claude/crypto-hpo-20261010`.

## Selected feature space

| Class | Models | Families kept | Scale | Columns |
|---|---|---|---|---|
| tabular | MLP | `vol`, `clock`, `bvol` | 60 min | 13 |
| sequence | PatchTST, 1D-CNN, GRU, Transformer | `vol`, `clock` | 1440 min | 10 |

Dropped in both classes: `ret`, `trend`, `range`, `shock`, `flow`, `holiday`, `xasset`. `bvol` (Binance implied vol) was kept for tabular only (t = 1.6) and dropped for sequence (t = -0.6). Sequence columns: `vol__rms_1440`, `vol__hl_1440`, `vol__jump_1440`, `clock__tod_sin`, `clock__tod_cos`, `clock__dow_sin`, `clock__dow_cos`, `clock__month_end`, `clock__us_open`, `clock__funding_min`. The sequence models also read the raw 60-bar window channels directly, which the selection does not touch.

Reading: the kept set is short volatility levels plus the clock, and the keep rule (z = 1) leans toward keeping, so dropping that many families is notable. It is consistent with returns at these horizons carrying little predictable signal beyond the volatility level.

## Results (mean weighted CRPS over 14 folds; lower is better)

| Cell | Mean | vs best baseline |
|---|---|---|
| **gru-crps_tail-pooled (pick)** | **2.2470** | **-0.0250 (SE 0.0008), -1.10%** |
| gru-nll-pooled | 2.2470 | -0.0250 (near-tie, gap 0.00002) |
| gru-crps-pooled | 2.2475 | -0.0244 |
| patchtst-nll-pooled | 2.2501 | -0.0219 |
| cnn1d-nll-pooled | 2.2513 | -0.0206 |
| transformer-nll / unpooled | 2.2548 | -0.0171 |
| mlp-nll-pooled | 2.2579 | -0.0140 |
| empirical (baseline) | 2.2719 | 0 |
| EWMA-Student (baseline) | 2.5873 | +0.3154 |

Per-fold gain over the empirical baseline: -0.82% to -1.31%, better in 14 of 14 folds. Overall metrics for the pick versus the empirical baseline: CRPS 1.5560 vs 1.5711, tail CRPS 0.6910 vs 0.7008, pinball 0.6415 vs 0.6496, PIT KS 0.0133 vs 0.0258. The PIT p-value is 3e-4 for the pick (still rejects exact uniformity at this sample size) against about 1e-76 for the baseline, so calibration is much better but not perfect. Scopes are not compared by raw mean (a BTC-only or ETH-only lane scores different rows); the pick combines unpooled cells row-weighted, and the best pooled cell leads the best unpooled one (2.2470 vs 2.2485). The architecture matters more than the loss (best cell per architecture: GRU 2.2470, PatchTST 2.2501, 1D-CNN 2.2513, Transformer 2.2548, MLP 2.2579).

## Caveats

- The SE above treats folds as independent, but expanding folds share training data, so it is understated (open item; the nested-fold SE note).
- The gaps between the top architectures (0.003 to 0.01) are small against that SE; the GRU lead is not strongly established. One seed only.
- Stride 5 training is a deliberate compute deviation. Every-minute training is planned for the chosen model after HPO and for simulations.
- The EWMA-Student baseline is much worse than the empirical one; the empirical distribution is the real bar.
- The ADR-0261 keep rule, wing placement for the tail loss (provisional), BVOL `missing: mean` handling and the "already scored" W4 question remain open owner decisions.

## HPO result (2026-10-11)

HPO ran on W1 folds 1 to 4 (warmup, 2024-01-01 to 2024-08-28) for the picked cell: the zoo default plus the options child's bundles a to d (mapped onto the trunk and optimizer). The default won; nothing was tuned.

| Candidate | Warmup mean weighted CRPS |
|---|---|
| default | 2.25153 |
| c | 2.25183 |
| a | 2.25401 |
| b | 2.25419 |
| d | 2.25698 |

Frozen hyperparameters = the zoo default (`/home/russell/data/crypto_trading/hpo/hpo-frozen.json`, sha256 in the file). There is no tuned cell, so the tuned-versus-default evaluation on folds 5 to 14 recorded none (`hpo-evaluation.json`). The default's warmup fold scores equal the zoo run's exactly, so a rerun of the same code is deterministic here. A 5-candidate predeclared grid is a small search; a wider space is an owner decision.

## Handoff

- Next (running): HPO on W1 folds 1 to 4 for this cell (bundles a to d plus default), freeze, then compare tuned and default on folds 5 to 14 (no W2).
- Not yet run, by design: the production pipeline (initial train, periodic retrain, per-block models saved), because it reads W2 (single read) and W4/W5 are reserved. The W2 read needs the owner's go-ahead.
