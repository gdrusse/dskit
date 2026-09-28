## Question

On the 21 ADR-0187 SPY, QQQ, and IWM horizon cells, does a small GRU extract
more forward-realized-volatility signal from the prior 22 daily returns than a
small MLP, and is any advantage stable enough to guide the next feature screen?

## Finding

The GRU is the clear full-history architecture winner, but its advantage is
not stable in recent regimes. Keep it as the temporal challenger; do not treat
this run as evidence of absolute forecast skill or readiness to trade.

- All 42 JSON documents validated, planned, and completed: 21 cells times two
  architectures, 714 total model folds. The four-worker WSL run, including
  validation and two smoke checks, took 9 minutes 27 seconds.
- The GRU had lower mean validation MSE and lower across-fold standard
  deviation in all 21 cells. Median cell-level mean-MSE reduction versus the
  MLP was 58.9% (range 31.2% to 73.1%).
- On paired yearly folds, the GRU won 257 of 357 comparisons (72.0%); the
  median fold-level MSE reduction was 29.0%.
- The result is regime-dependent. From 2019 onward the GRU won only 57 of 126
  paired folds and 15 of 21 cell means; median cell-level mean-MSE reduction
  fell to 3.0%. It won only 7/21, 7/21, and 6/21 cell-fold comparisons in
  2022, 2023, and 2024 respectively.
- Full-history relative gains were strongest for SPY (68.0% mean across cells),
  then IWM (57.8%), then QQQ (43.2%). The apparent gain generally increased
  with horizon, but absolute MSE is not comparable across horizons because the
  forward-volatility target becomes smoother as its averaging window grows.

### Cell results

All scores are the mean final validation MSE across expanding yearly folds.
“Wins” counts paired folds where GRU MSE was lower than MLP MSE.

| Cell | MLP MSE | GRU MSE | GRU reduction | Wins |
|---|---:|---:|---:|---:|
| IWM 1 | 2.58358e-4 | 1.30156e-4 | 49.6% | 12/18 |
| IWM 2-3 | 2.19433e-4 | 1.01597e-4 | 53.7% | 14/18 |
| IWM 5 | 2.05317e-4 | 9.00360e-5 | 56.1% | 15/18 |
| IWM 7-10 | 1.92111e-4 | 7.90064e-5 | 58.9% | 15/18 |
| IWM 14 | 1.83415e-4 | 7.10655e-5 | 61.3% | 15/18 |
| IWM 21 | 1.82363e-4 | 6.69254e-5 | 63.3% | 16/18 |
| IWM 30-45 | 1.70207e-4 | 6.49592e-5 | 61.8% | 16/18 |
| QQQ 1 | 1.41840e-4 | 9.75680e-5 | 31.2% | 10/15 |
| QQQ 2-3 | 1.26529e-4 | 7.93821e-5 | 37.3% | 10/15 |
| QQQ 5 | 1.19656e-4 | 7.05723e-5 | 41.0% | 10/15 |
| QQQ 7-10 | 1.14325e-4 | 6.32057e-5 | 44.7% | 10/15 |
| QQQ 14 | 1.08375e-4 | 5.68580e-5 | 47.5% | 10/15 |
| QQQ 21 | 1.06153e-4 | 5.35291e-5 | 49.6% | 10/15 |
| QQQ 30-45 | 1.04521e-4 | 5.08566e-5 | 51.3% | 10/15 |
| SPY 1 | 2.29358e-4 | 9.33718e-5 | 59.3% | 12/18 |
| SPY 2-3 | 2.15295e-4 | 7.60207e-5 | 64.7% | 12/18 |
| SPY 5 | 2.09664e-4 | 6.92891e-5 | 67.0% | 12/18 |
| SPY 7-10 | 2.05890e-4 | 6.37246e-5 | 69.0% | 12/18 |
| SPY 14 | 2.01000e-4 | 5.81170e-5 | 71.1% | 12/18 |
| SPY 21 | 1.99159e-4 | 5.55545e-5 | 72.1% | 12/18 |
| SPY 30-45 | 1.98117e-4 | 5.32086e-5 | 73.1% | 12/18 |

### Regime audit

The large full-history improvement is partly a reduction in severe early-fold
MLP errors. The 2009 and 2011 IWM long-horizon folds contain MLP-to-GRU loss
ratios above 10x. The edge remains positive after removing those years, but it
decays materially:

| Validation years | GRU cell-mean wins | GRU fold wins | Median cell reduction |
|---|---:|---:|---:|
| Full sample | 21/21 | 257/357 | 58.9% |
| 2012 onward | 21/21 | 187/273 | 48.7% |
| 2015 onward | 21/21 | 124/210 | 24.8% |
| 2019 onward | 15/21 | 57/126 | 3.0% |

This is evidence that the return path contains temporal structure a recurrent
model can exploit in some regimes. It is not yet evidence that the GRU adds
information beyond the existing HAR plus underlying-implied-volatility bar.

## Setup and limits

- Inputs were only the prior 22 daily log returns. The target was forward
  per-session realized volatility at each cell's label horizon.
- The fixed MLP used one 16-unit hidden layer and six epochs. The fixed GRU
  used eight hidden units, one layer, and 60 epochs. Adam, learning rate 0.003,
  batch size 512, seed 0, and CPU execution were shared. Schedules were frozen
  after one bounded IWM-1 smoke fold.
- The score was final validation MSE. This run did not evaluate a distribution,
  twCRPS, strike-region calibration, option returns, or trading P&L.
- MLP and GRU had different epoch budgets to reach comparable convergence.
  That makes this a practical architecture screen, not a controlled attribution
  of the gain solely to recurrence. There was no seed replication or nested HPO.
- The empirical, HAR, HAR-IV, and LightGBM rungs were not rerun under this MSE
  target. The MLP is a challenger, not the empirical baseline, so absolute
  skill over the hard baseline remains unmeasured here.

## Decision

1. Retain the small GRU as the temporal-path challenger and stop spending
   cycles on this simple MLP specification.
2. Do not promote either model or connect it to a trading decision.
3. Run nested, walk-forward feature-family ablations against the existing HAR
   plus underlying-IV baseline. Add features in this order: option-surface and
   underlying-specific implied-volatility structure; signed realized-state
   features; rates/funding; then cross-market stress.
4. Require improvement in recent folds, not only the full-history mean. The
   minimum promotion bar is stable proper-score improvement on 2019-present
   folds and no systematic deterioration in 2022-2024.
5. Only after a feature set clears that bar should the GRU produce a full
   conditional distribution and face the locked strike-zone twCRPS evaluator.

## Reproducibility

- Run documents and artifacts:
  `pipeline_runs/temporal_feature_selection/`
- Full-run window: 2026-09-28 08:30:45-04:00 through 08:40:12-04:00.
- The automatic decision journal contains the 42 full walk-forward records and
  the two final smoke records. Pipeline artifacts remain local and ignored;
  this memo is the durable result summary.
