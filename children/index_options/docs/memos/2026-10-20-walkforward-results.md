# ADR-0270 walk-forward results: yearly retraining against the market and baselines (2026-10-20)

## TL;DR

Trained properly (five yearly retrains, 50+ epochs each), the TCN chain-arm model **beats the per-name baseline in every validation year (+0.2% to +2.7%) but does not beat the raw market overall (-0.7%, t -2.2)**. It beats the market in the calm 2021 recovery (+2.2%) and loses in stress: the 2022 rate shock (-4.4%, t -7.2) and the 2020 crash (-2.5%). Training length is not the issue: validation loss bottoms in epoch 1-2 and rises for the next 50 while training loss falls, so the model learns a little and then memorizes. Per-(ticker, expiry) results are at chance: **75 of 831 cells pass the gate, 76 expected by luck.**

## What ran (contract)

- Predictability only; no P&L. Holdout 2024-25 sealed and unread. Path rows A0001-A0008 (locked) govern this run.
- Model: TCN encoder (hidden 16, kernel 2, dilations 1-16, width 16), chain arm (66 inputs: 22 raw daily returns, 16 context scalars incl. DTE terms, 19 market quantiles + interior mass + 8 chain scalars), 3-component Gaussian mixture, 116 per-ticker heads, 12.3k parameters. Losses CRPS + tail CRPS. AdamW, lr 3e-4, weight decay 0.01, dropout 0.1, batch 512, **patience 50, ceiling 150**, seed 11. Variant: same with lr 1e-4.
- Folds (retrained before each): warm-up train 2017-18, validate 2019 (reported, never ranked); then train through 2019 to validate 2020, through 2020 to 2021, through 2021 to 2022, through 2022 to 2023. Monitor band = last 82 origins of each train window; labels purged at each boundary.
- Comparators on identical rows: **raw market-implied curve** (market-eligible rows, 63% of validation rows), **per-name horizon baseline**, **pooled standardized empirical**.
- Headline score (A0002): threshold-weighted CRPS on the 13-point z grid (weight 1 on |z| 0.5-2.5, tapering to 0.25 at z=0 and |z|>=3), beside plain CRPS, tail CRPS, strike Brier and wing twCRPS. Edge = (comparator - model) / comparator; positive = the model has the lower loss. SE = larger of date and symbol clustering.
- Rows scored: validation 711,185 (449,498 market-eligible, 110 names, 1,005 dates, folds 2-5); training windows up to 100,000 rows per fold (367,393). Cost: fits 20 min, scoring 21 min per run, peak 9.3 GiB; second packet used 4,927 of 14,400 s.

## 1. How the model trains

| Fold (validate) | Epochs run | Best epoch | Train loss first -> last | Monitor first / best / last |
|---|---|---|---|---|
| 1 (2019, warm-up) | 51 | 1 | 1.020 -> 0.788 | 1.307 / 1.307 / 1.400 |
| 2 (2020) | 52 | 2 | 1.029 -> 0.843 | 0.908 / 0.903 / 0.988 |
| 3 (2021) | 51 | 1 | 1.048 -> 0.895 | 0.949 / 0.949 / 1.076 |
| 4 (2022) | 52 | 2 | 1.017 -> 0.893 | 1.049 / 1.048 / 1.095 |
| 5 (2023) | 52 | 2 | 1.013 -> 0.909 | 0.953 / 0.950 / 0.969 |

- Every fold trained at least 51 epochs (the earlier screen stopped at 11-13). Train loss falls the whole time; the monitor loss (the most recent 82 origins of each training window) is lowest at epoch 1-2 and rises monotonically after. Example, fold 2: monitor 0.908, 0.903, 0.904 (epochs 1-3), 0.935 (10), 0.960 (20), 0.988 (52) while train falls 1.029 -> 0.843.
- Reading: the model **does learn** (train loss falls 10-22% and the checkpoint at epoch 1-2 beats the epoch-0 monitor loss, e.g. 0.903 vs 0.946), but what generalizes is learned in about 1,000-2,000 optimizer steps; the rest is memorization (116 ticker heads and 12k parameters on correlated rows).
- Learning rate 1e-4 (second run) moves the best epoch to 4-9 (folds 2-5: 7, 4, 4, 9) and gives the same validation results (below): the early peak is not an artefact of the step size.

## 2. Performance against the market and baselines (twCRPS edge, positive = model better)

| Window | vs raw market | vs per-name baseline | vs pooled |
|---|---|---|---|
| Training windows, folds 2-5 (in-sample) | **+4.2%** (t 13.2) | +2.1% | +5.6% |
| Validation 2020-23 pooled | **-0.7%** (SE 0.30, t -2.2) | **+1.3%** (t 7.2) | +0.4% (t 1.6) |
| 2019 warm-up (not ranked) | -1.9% | +1.3% | 0.0% |
| 2020 | 0.0% | +2.7% | +0.5% |
| 2021 | **+2.2%** (t 3.1) | +0.2% | +1.0% |
| 2022 | **-4.4%** (t -7.2) | +1.6% | -1.0% |
| 2023 | -0.6% | +0.7% | +1.1% |
| Regime S0 pre-crash (Jan-Feb 2020) | -3.4% | +2.2% | +1.5% |
| S1 COVID crash (to Jun 2020) | -2.5% | +2.9% | -0.6% |
| S2 recovery (to 2021) | +2.5% | +1.0% | +0.9% |
| S3 rate shock (2022) | -4.4% | +1.6% | -1.0% |
| S4 2023 | -0.6% | +0.7% | +1.1% |

Other metrics, validation 2020-23 pooled (market / baseline / pooled): plain CRPS -0.7 / +1.1 / 0.0; tail CRPS -0.6 / +1.5 / +1.0; strike Brier -0.4 / +1.1 / -0.5; wing twCRPS -0.5 / +1.6 / -0.1. Same signs and sizes in the lr 1e-4 run (twCRPS -0.6 / +1.3 / +0.4).

- In-sample the model beats the market by 4.2%; out of sample that becomes -0.7%. The 5-point gap is the overfit.
- The model's edge is regime dependent: it beats the market in calm, recovering markets and loses in stress and rate shocks, where the market reprices faster than a model trained on the past.
- Against the per-name baseline it wins in all five years, but the baseline is the easy bar; the pooled empirical is a tie (+0.4%, t 1.6).

## 3. By expiry rank (weekly names, 2020-23)

| Rank (k-th weekly expiry) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Edge vs market | -2.1% | -1.4% | -0.9% | -0.4% | +0.1% | +0.6% | +0.2% | +1.0% |
| t vs market | -8.3 | -5.1 | -3.0 | -1.2 | 0.3 | 1.3 | 0.6 | 1.3 |
| Edge vs baseline | +1.6% | +1.1% | +0.8% | +0.8% | +1.0% | +1.3% | +1.9% | +2.2% |

The market is better at short horizons; the model ties or edges it from rank 5 (about 4 weeks out). At rank 8 the year split is +5.6% (2021) and +2.2% (2023) against -4.1% (2022). The 26 monthly-only names lose to the market at every position (-2.3% to -6.5%).

## 4. Calibration (share of outcomes below the 5th and above the 95th percentile; nominal 5%)

| Validation 2020-23 | Below 5% | Above 95% |
|---|---|---|
| Model | 7.9% | 5.9% |
| Per-name baseline | 8.5% | 10.2% |
| Pooled empirical | 4.8% | 4.7% |
| Raw market | 4.7% | 9.0% |

The model is too narrow in the lower tail (7.9% vs 5%) but much closer than the per-name baseline; the raw market is calibrated on the downside and too narrow on the upside (9.0%), consistent with the variance-premium story and the reason a recalibrated market is the right production comparator.

## 5. By ticker and expiry (the A0007 gate)

- Gate: beat the market AND the per-name baseline in >= 3 of 4 years and >= 3 of 5 regimes, per (ticker, rank). 831 cells (732 weekly, 99 monthly). **75 pass; 76.3 expected by luck (95th percentile 89)**: no signal beyond chance. lr 1e-4: 66 pass (76.2 expected). 49 cells pass in both runs; 26 beat the market in all four years.
- Strongest cells (>= 300 dates, passing, edge vs market): AVGO ranks 5-7 (+11% to +12%, vs baseline +3% to +4%), KKR rank 7 (+8.8%), DE ranks 5-6 (+5.8%), AAPL rank 7 (+5.7%), CMG rank 7 (+5.3%), AMAT ranks 5-6 (+3% to +5%), COF rank 6 (+4.3%). The pattern is consistent with the earlier screen (AVGO, KKR, CMG, AAPL, AMAT, COST) and with the rank profile (edges at ranks 5-8), but with 831 cells and chance-level pass counts these are leads, not findings.
- Power: about 120-200 independent expiries per cell cannot detect an edge under about 4% per ticker (Opus research, 2026-10-20); no multiplicity correction is applied yet (A0007), so the count against luck is the honest reading.

## 6. Caveats

- Comparator is the raw risk-neutral market, not a recalibrated one; the in-sample and tail results above say the recalibrated market will be harder to beat.
- One seed; no seed-noise floor yet. The lr 1e-4 run is a sensitivity check, not a second seed.
- The scored model is the best-monitor checkpoint (epoch 1-2); the long schedule only records the curves.
- Design pieces still not built: vol-unit returns, the 13 logit-difference chain vector, a shared head, the residual arm. Per-ticker heads are the likeliest memorizer.
- Market-eligible rows are 63% of validation rows; baseline comparisons on all rows are in the CSVs (`rowset = all`).
- Holdout 2024-25 not read; the gate has no multiplicity correction by design (A0007).

## 7. What this means for production

Not yet a production candidate: the model adds a small, robust edge over a per-name baseline, ties the pooled empirical, and loses to the raw market overall, with the loss concentrated in stress and at short horizons. The next gates are (1) the recalibrated market, (2) fixing the overfit (shared head, stronger regularization, early stop at epoch 1-2), (3) regime information as an input, since the edge flips sign with the regime.

## Files

`data/2026-10-20-walkforward/` (long CSVs; prefix `wf_tcn_` main, `wf_tcn_lr1e4_` sensitivity): `training_curves` (per fold and epoch), `perf_by_window` (training and validation windows, folds, years, regimes, pooled; 5 metrics x 3 comparators x market-eligible/all rows, with SE and t), `perf_by_rank`, `cells_ticker_rank_year` (4,555 cells), `gate_ticker_rank`, `gate_summary`, `calibration_by_window`; `analysis_wf.py` regenerates them from the pilot-root scores. Pilot root: /home/russell/data/index_options/theta-strike-screen-20261018 (scores/strike-region-scores-wf-tcn*, fits/wf-tcn*). Documents: `configs/{strike-screen-folds-walkforward.jsonl, run-strike-screen-fits-walkforward.json, run-strike-region-scores-walkforward.json}`.
