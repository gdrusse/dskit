## Correction and final model

The final exact-expiry variability model is **ElasticNet**, fitted separately
for SPY, QQQ, and IWM with shared hyperparameters `alpha=0.03` and
`l1_ratio=0.75`. It forecasts daily RMS volatility over each row's actual
quote-to-expiry sessions. Exact integrated variance is a deterministic derived
output, `daily_rms_hat² × sessions_to_expiry`.

This replaces the first version of this memo. That version learned raw summed
variance with one cross-maturity model per index, which unfairly required
linear models to learn a multiplicative horizon relationship. The resulting
low Ridge/ElasticNet scores and TCN selection are superseded.

The tables below now include the observation count for every exact-DTE cell
and explicit training/validation sizes for every development and evaluation
fold.

## What one prediction means

For index (i), after-close quote date (t), and exact expiry (e), let
(S(e)) be the last trading session on or before expiry and (N_{t,e}) the
number of future trading sessions. The learned target is

\[
y_{i,t,e}=\sqrt{\frac{1}{N_{t,e}}
\sum_{s=t+1}^{S(e)}\left[\log(P_{i,s}/P_{i,s-1})\right]^2}.
\]

`daily_rms_hat` is one scalar in daily log-return volatility units. The output
also carries exact expiry metadata and two deterministic transforms:

\[
\widehat V_{t,e}=N_{t,e}\widehat y_{t,e}^{,2},\qquad
\widehat\sigma_{annual}=\sqrt{252}\widehat y_{t,e}.
\]

The output schema is:

| Column | Meaning |
|---|---|
| `symbol` | SPY, QQQ, or IWM |
| `quote_date` | after-close forecast date |
| `expiry` | exact archive expiration date |
| `settlement_date` | last trading session on or before expiry |
| `calendar_dte` | actual calendar days to settlement |
| `sessions_to_expiry` | actual future trading sessions |
| `daily_rms_hat` | learned exact-window daily RMS forecast |
| `integrated_variance_hat` | `daily_rms_hat² × sessions_to_expiry` |
| `annualized_vol_hat` | `daily_rms_hat × sqrt(252)` |

For example, on 2025-11-12 the 2025-12-12 expiry was exactly 30 calendar days
and 21 trading sessions away:

| Index | Daily RMS forecast | Integrated variance | Annualized interpretation |
|---|---:|---:|---:|
| SPY | 0.8603% | 0.001554 | 13.66% |
| QQQ | 1.2675% | 0.003374 | 20.12% |
| IWM | 1.1962% | 0.003005 | 18.99% |

A 30-day and 45-day option are separate rows with their actual expiry and
session count. No horizon is approximated to 22 sessions.

## What the final model looks like

Every index model uses the same 38 columns:

- `ret_lag_0` through `ret_lag_21`;
- `rv_1`, `rv_5`, `rv_22`, and `rv_66`;
- `own_iv`: VIX for SPY, VXN for QQQ, RVX for IWM;
- calendar and trading-session DTE plus their log transforms;
- first-seen series age and total tenor in calendar and session units;
- calendar/session elapsed-life fractions;
- calendar days per remaining trading session.

First-seen series age is a causal archive proxy because the source does not
provide the exchange's authoritative listing timestamp.

Training-fold medians replace missing inputs. Means and scales are fitted on
training rows only. For standardized inputs (z_j) and target mean/scale
((\mu_y,s_y)), the forecast is

\[
\widehat y=\max\left(0,\mu_y+s_y\left[b+\sum_{j=1}^{38}\beta_jz_j\right]\right).
\]

The coefficients minimize

\[
\frac{1}{2n}\lVert z_y-X_z\beta\rVert_2^2
+0.03\left(0.75\lVert\beta\rVert_1
+0.125\lVert\beta\rVert_2^2\right).
\]

The L1-heavy mixture removes redundant features while the L2 component
stabilizes correlated lags and maturity variables. The full-history
explanatory refits are:

| Index | Labeled rows | Date span | Nonzero coefficients | Largest standardized effects |
|---|---:|---|---:|---|
| SPY | 44,770 | 2008-01-02–2025-12-11 | 9/38 | `own_iv +0.387`, `rv_5 +0.242`, `ret_lag_1 -0.066`, `ret_lag_0 -0.055`, `log_sessions +0.051` |
| QQQ | 32,639 | 2011-03-23–2025-12-12 | 11/38 | `own_iv +0.434`, `rv_5 +0.133`, `log_sessions +0.067`, `rv_22 +0.060`, `ret_lag_1 -0.057` |
| IWM | 31,946 | 2008-01-02–2025-12-12 | 15/38 | `rv_22 +0.214`, `own_iv +0.177`, `rv_5 +0.173`, `log_sessions +0.078`, `ret_lag_1 -0.069` |

The negative recent-return coefficients are consistent with the familiar
leverage/asymmetry effect. Unlike the rejected raw-variance TCN, the final
ElasticNet produces no zero-clamped validation predictions.

These full-history fits describe model shape. Every skill number below comes
only from out-of-sample annual validation forecasts.

## Development, HPO, and validation procedure

There are three disjoint roles for the rows:

1. **Feature-family development, 2013-2018.** Eight bundles were screened with
   Ridge and LightGBM. This selected the 38-feature core-maturity set.
2. **Architecture comparison, 2019-2025.** Eighteen models used identical rows
   and selected features. Each architecture produced 68,084 validation
   forecasts across 21 index-year folds.
3. **ElasticNet HPO, 2013-2018.** Ninety endpoint-inclusive candidates used the
   development folds only: 1,620 fits and 33,528 validation predictions per
   candidate. The selected pair was then evaluated on 2019-2025.

For every fold:

- features end at the quote close;
- a training row is admitted only when its target settlement ends strictly
  before January 1 of the validation year;
- the underlying-price archive must cover the entire expiry window;
- feature imputation, feature scaling, and target scaling use training rows
  only;
- the model fits once and forecasts the whole validation year without
  within-year refitting.

The empirical baseline is the expanding-training mean daily RMS conditioned
on exact calendar DTE. The persistence baseline is trailing RMS over exactly
the same number of trading sessions as the future window.

### Development fold sizes

Each entry is `training rows / validation rows`. Validation totals 33,528 rows.

| Year | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 2013 | 3,129 / 1,406 | 1,312 / 1,406 | 3,125 / 1,396 |
| 2014 | 4,528 / 1,717 | 2,708 / 1,716 | 4,511 / 1,717 |
| 2015 | 6,205 / 1,728 | 4,384 / 1,711 | 6,188 / 1,717 |
| 2016 | 7,959 / 2,139 | 6,123 / 1,682 | 7,931 / 1,689 |
| 2017 | 10,028 / 2,931 | 7,799 / 1,620 | 9,616 / 1,620 |
| 2018 | 12,959 / 4,051 | 9,419 / 1,641 | 11,236 / 1,641 |

### Final evaluation fold sizes

Each entry is `training rows / validation rows`. Validation totals 68,084
rows: 27,608 SPY, 21,493 QQQ, and 18,983 IWM.

| Year | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 2019 | 16,946 / 4,246 | 11,055 / 1,677 | 12,872 / 1,669 |
| 2020 | 21,197 / 4,316 | 12,724 / 1,717 | 14,533 / 1,717 |
| 2021 | 25,509 / 4,296 | 14,458 / 3,506 | 16,267 / 2,334 |
| 2022 | 29,791 / 4,230 | 17,818 / 4,173 | 18,455 / 4,077 |
| 2023 | 34,135 / 3,505 | 22,104 / 3,495 | 22,655 / 2,592 |
| 2024 | 37,626 / 3,691 | 25,595 / 3,591 | 25,244 / 3,303 |
| 2025 | 41,317 / 3,324 | 29,176 / 3,334 | 28,526 / 3,291 |

The HPO grid included exact Ridge and Lasso endpoints. Its deterministic
closed-form/FISTA solver passed a `1e-5` KKT residual check on all 1,620 fits;
the worst residual was `9.997e-6`.

## Aggregate skill

Skill is `1 - model SSE / baseline SSE`; positive is better.

| Period | Index | Observations | Equal-day skill | Pooled skill vs DTE mean | Pooled skill vs persistence | Positive days |
|---|---|---:|---:|---:|---:|---:|
| 2019-2025 | SPY | 27,608 | 41.92% | 45.13% | 34.28% | 44/45 |
| 2019-2025 | QQQ | 21,493 | 47.32% | 45.51% | 36.11% | 45/45 |
| 2019-2025 | IWM | 18,983 | 31.86% | 33.40% | 35.41% | 44/45 |
| 2022-2025 | SPY | 14,750 | 43.37% | 40.50% | 39.50% | 45/45 |
| 2022-2025 | QQQ | 14,593 | 52.61% | 47.65% | 37.19% | 45/45 |
| 2022-2025 | IWM | 13,263 | 20.80% | 19.15% | 40.14% | 41/45 |

Overall equal-index/day skill is 40.37%; pooled skill is 42.24% versus the
DTE-conditioned mean and 35.19% versus matched persistence. Recent
equal-index/day skill is 38.93%.

## Exact-day skill versus the DTE-conditioned mean, 2019-2025

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | 31.8% | 18.2% | 15.1% |
| 2 | 43.8% | 29.9% | 22.6% |
| 3 | 38.7% | 30.9% | 29.2% |
| 4 | 50.1% | 40.3% | 32.6% |
| 5 | 49.3% | 41.1% | 15.6% |
| 6 | 53.9% | 45.3% | 25.2% |
| 7 | 52.4% | 44.5% | 33.5% |
| 8 | 52.8% | 47.0% | 38.3% |
| 9 | 51.1% | 44.1% | 36.4% |
| 10 | 50.9% | 47.3% | 41.5% |
| 11 | 55.1% | 51.4% | 43.3% |
| 12 | 52.3% | 54.0% | 28.5% |
| 13 | 52.4% | 49.7% | 24.3% |
| 14 | 49.4% | 47.0% | 35.4% |
| 15 | 53.2% | 56.7% | 48.3% |
| 16 | 49.6% | 50.3% | 42.8% |
| 17 | 50.2% | 52.9% | 47.1% |
| 18 | 53.0% | 56.0% | 48.8% |
| 19 | 52.6% | 73.3% | 43.7% |
| 20 | 53.4% | 65.1% | 47.8% |
| 21 | 48.7% | 55.9% | 45.5% |
| 22 | 50.1% | 55.8% | 47.4% |
| 23 | 44.7% | 48.0% | 39.5% |
| 24 | 43.7% | 51.3% | 42.5% |
| 25 | 46.0% | 54.4% | 41.2% |
| 26 | 44.5% | 69.5% | 17.8% |
| 27 | 45.9% | 61.2% | 33.0% |
| 28 | 43.0% | 53.9% | 39.4% |
| 29 | 41.1% | 49.8% | 37.9% |
| 30 | 38.9% | 44.9% | 33.6% |
| 31 | 39.0% | 49.0% | 39.1% |
| 32 | 39.7% | 51.5% | 38.0% |
| 33 | 38.9% | 69.8% | 11.7% |
| 34 | 39.9% | 60.9% | 21.1% |
| 35 | 36.5% | 50.6% | 35.6% |
| 36 | 34.4% | 46.3% | 31.6% |
| 37 | 28.6% | 37.7% | 27.8% |
| 38 | 32.8% | 43.8% | 33.6% |
| 39 | 30.5% | 42.0% | 31.2% |
| 40 | -1.2% | 13.6% | -17.9% |
| 41 | 22.7% | 30.6% | 10.9% |
| 42 | 28.1% | 38.7% | 25.0% |
| 43 | 26.2% | 38.1% | 25.4% |
| 44 | 21.1% | 31.0% | 17.4% |
| 45 | 26.6% | 36.3% | 25.2% |

### Observation count for each 2019-2025 cell

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | 1,003 | 886 | 729 |
| 2 | 831 | 724 | 635 |
| 3 | 809 | 706 | 626 |
| 4 | 807 | 689 | 604 |
| 5 | 841 | 612 | 505 |
| 6 | 1,006 | 774 | 598 |
| 7 | 1,321 | 1,096 | 923 |
| 8 | 1,018 | 892 | 741 |
| 9 | 827 | 720 | 631 |
| 10 | 806 | 704 | 624 |
| 11 | 805 | 687 | 600 |
| 12 | 838 | 609 | 500 |
| 13 | 1,001 | 769 | 590 |
| 14 | 1,291 | 1,065 | 900 |
| 15 | 591 | 461 | 429 |
| 16 | 547 | 438 | 414 |
| 17 | 544 | 438 | 416 |
| 18 | 520 | 400 | 376 |
| 19 | 410 | 174 | 127 |
| 20 | 429 | 190 | 142 |
| 21 | 752 | 521 | 475 |
| 22 | 582 | 452 | 424 |
| 23 | 546 | 434 | 411 |
| 24 | 543 | 435 | 413 |
| 25 | 518 | 397 | 373 |
| 26 | 407 | 171 | 125 |
| 27 | 426 | 185 | 140 |
| 28 | 748 | 511 | 467 |
| 29 | 579 | 451 | 424 |
| 30 | 543 | 432 | 409 |
| 31 | 539 | 431 | 410 |
| 32 | 517 | 396 | 371 |
| 33 | 403 | 167 | 121 |
| 34 | 427 | 185 | 139 |
| 35 | 746 | 513 | 468 |
| 36 | 576 | 444 | 419 |
| 37 | 368 | 362 | 358 |
| 38 | 536 | 426 | 405 |
| 39 | 348 | 332 | 326 |
| 40 | 23 | 16 | 12 |
| 41 | 40 | 31 | 27 |
| 42 | 370 | 361 | 359 |
| 43 | 364 | 355 | 352 |
| 44 | 236 | 229 | 225 |
| 45 | 226 | 222 | 220 |

DTE 40 and 41 have exceptionally low coverage because of real listing
patterns. Their skill estimates are not stable and should not drive a decision.

## Exact-day skill versus matched-session persistence, 2019-2025

The observation counts are identical to the preceding count grid.

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | 50.1% | 48.0% | 48.2% |
| 2 | 36.5% | 37.4% | 36.3% |
| 3 | 31.1% | 41.7% | 39.4% |
| 4 | 37.5% | 51.9% | 45.8% |
| 5 | 28.4% | 50.4% | 49.6% |
| 6 | 29.1% | 36.5% | 42.6% |
| 7 | 24.7% | 27.1% | 34.3% |
| 8 | 26.3% | 28.4% | 36.0% |
| 9 | 22.1% | 23.0% | 27.0% |
| 10 | 25.1% | 32.7% | 34.9% |
| 11 | 34.2% | 41.3% | 43.0% |
| 12 | 28.3% | 40.6% | 48.3% |
| 13 | 34.4% | 37.8% | 44.9% |
| 14 | 32.2% | 36.1% | 36.2% |
| 15 | 32.5% | 36.3% | 31.5% |
| 16 | 31.0% | 30.2% | 28.6% |
| 17 | 25.7% | 29.1% | 26.5% |
| 18 | 32.5% | 28.8% | 29.7% |
| 19 | 26.1% | 14.8% | 25.8% |
| 20 | 29.9% | 10.7% | 23.8% |
| 21 | 28.9% | 20.9% | 21.5% |
| 22 | 35.2% | 31.1% | 28.0% |
| 23 | 34.2% | 25.6% | 26.2% |
| 24 | 33.7% | 33.0% | 30.4% |
| 25 | 38.7% | 36.4% | 31.1% |
| 26 | 29.2% | -6.8% | 9.1% |
| 27 | 30.8% | -27.2% | -0.1% |
| 28 | 35.2% | 24.5% | 23.1% |
| 29 | 36.5% | 29.4% | 27.7% |
| 30 | 39.2% | 30.1% | 28.4% |
| 31 | 37.6% | 38.4% | 33.4% |
| 32 | 42.1% | 40.7% | 34.8% |
| 33 | 33.6% | -1.9% | 19.6% |
| 34 | 36.7% | -3.5% | 13.0% |
| 35 | 36.8% | 26.4% | 25.4% |
| 36 | 39.1% | 33.0% | 30.0% |
| 37 | 37.7% | 37.5% | 30.8% |
| 38 | 36.2% | 37.3% | 33.3% |
| 39 | 48.9% | 42.9% | 35.0% |
| 40 | -1.8% | 1.5% | -0.2% |
| 41 | 15.3% | 17.9% | 14.7% |
| 42 | 35.2% | 38.6% | 28.7% |
| 43 | 40.3% | 42.1% | 33.4% |
| 44 | 32.4% | 34.4% | 25.2% |
| 45 | 36.4% | 38.9% | 29.8% |

## Recent exact-day skill versus the DTE mean, 2022-2025

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | 20.5% | 18.4% | 8.0% |
| 2 | 32.2% | 30.2% | 11.8% |
| 3 | 29.5% | 28.9% | 13.5% |
| 4 | 35.7% | 33.9% | 14.0% |
| 5 | 38.6% | 41.5% | 14.0% |
| 6 | 43.2% | 44.5% | 18.5% |
| 7 | 44.3% | 42.3% | 19.3% |
| 8 | 41.8% | 41.4% | 17.1% |
| 9 | 39.7% | 42.3% | 16.8% |
| 10 | 43.9% | 45.9% | 21.2% |
| 11 | 44.2% | 47.2% | 21.8% |
| 12 | 45.0% | 53.4% | 24.6% |
| 13 | 41.4% | 49.6% | 18.1% |
| 14 | 39.4% | 45.0% | 16.0% |
| 15 | 43.1% | 56.4% | 29.2% |
| 16 | 44.7% | 57.5% | 27.6% |
| 17 | 46.9% | 58.1% | 32.9% |
| 18 | 50.0% | 62.1% | 31.8% |
| 19 | 56.8% | 74.8% | -3.3% |
| 20 | 49.9% | 70.3% | 10.1% |
| 21 | 52.2% | 64.4% | 38.6% |
| 22 | 51.1% | 61.0% | 32.8% |
| 23 | 45.3% | 57.7% | 25.3% |
| 24 | 46.3% | 60.1% | 31.5% |
| 25 | 48.8% | 63.1% | 32.5% |
| 26 | 66.1% | 78.2% | -0.1% |
| 27 | 50.3% | 69.2% | -6.1% |
| 28 | 49.0% | 63.1% | 39.1% |
| 29 | 47.6% | 60.5% | 35.5% |
| 30 | 41.9% | 57.2% | 27.5% |
| 31 | 45.6% | 60.4% | 31.9% |
| 32 | 47.7% | 62.9% | 34.3% |
| 33 | 56.6% | 76.0% | -24.2% |
| 34 | 49.7% | 71.3% | 5.0% |
| 35 | 49.6% | 64.5% | 42.8% |
| 36 | 46.2% | 60.0% | 36.0% |
| 37 | 35.0% | 47.8% | 14.7% |
| 38 | 45.4% | 60.1% | 34.8% |
| 39 | 37.8% | 52.5% | 19.3% |
| 40 | 27.4% | 0.5% | 53.5% |
| 41 | 32.9% | 28.8% | 13.7% |
| 42 | 41.5% | 52.7% | 22.2% |
| 43 | 39.9% | 51.6% | 19.5% |
| 44 | 30.6% | 46.8% | 2.7% |
| 45 | 36.7% | 53.2% | 10.4% |

### Observation count for each 2022-2025 cell

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | 693 | 693 | 558 |
| 2 | 534 | 534 | 464 |
| 3 | 517 | 518 | 459 |
| 4 | 514 | 515 | 455 |
| 5 | 534 | 535 | 474 |
| 6 | 691 | 692 | 561 |
| 7 | 869 | 870 | 740 |
| 8 | 695 | 695 | 569 |
| 9 | 531 | 531 | 461 |
| 10 | 513 | 514 | 455 |
| 11 | 512 | 513 | 451 |
| 12 | 531 | 532 | 469 |
| 13 | 684 | 685 | 551 |
| 14 | 840 | 839 | 717 |
| 15 | 268 | 264 | 257 |
| 16 | 250 | 248 | 243 |
| 17 | 252 | 250 | 249 |
| 18 | 228 | 226 | 227 |
| 19 | 102 | 97 | 96 |
| 20 | 112 | 106 | 103 |
| 21 | 301 | 295 | 292 |
| 22 | 259 | 254 | 251 |
| 23 | 249 | 245 | 241 |
| 24 | 250 | 247 | 246 |
| 25 | 227 | 224 | 225 |
| 26 | 100 | 94 | 94 |
| 27 | 109 | 102 | 102 |
| 28 | 296 | 288 | 287 |
| 29 | 257 | 251 | 249 |
| 30 | 246 | 242 | 239 |
| 31 | 247 | 243 | 243 |
| 32 | 224 | 222 | 223 |
| 33 | 97 | 91 | 91 |
| 34 | 109 | 101 | 100 |
| 35 | 295 | 287 | 286 |
| 36 | 255 | 248 | 246 |
| 37 | 207 | 202 | 199 |
| 38 | 245 | 239 | 239 |
| 39 | 189 | 186 | 186 |
| 40 | 14 | 6 | 5 |
| 41 | 23 | 14 | 12 |
| 42 | 210 | 202 | 201 |
| 43 | 205 | 198 | 196 |
| 44 | 134 | 128 | 125 |
| 45 | 132 | 127 | 126 |

The recent negative IWM cells at DTE 19, 26, 27, and 33 have only 96, 94,
102, and 91 observations respectively. They are meaningful cautions, but much
less precisely estimated than the high-frequency weekly cells.

## What exists today

The corrected local evidence contains all fold metrics, HPO diagnostics,
68,084 out-of-sample prediction rows, exact-day skill and count grids, and
three explanatory full-history coefficient/scaler descriptions.
`final_elasticnet_predictions.parquet` is research evidence, not a released
model. There is no production serialization, freshness contract, calibrated
distribution head, option payoff model, or entry node.

The next honest evidence is a future fold. The next modeling layer is a
calibrated return distribution conditional on this scale forecast. Neither is
permission to trade.

## Evidence

Corrected ignored evidence is under
`children/index_options/pipeline_runs/exact_maturity_rms_20260928/`:

- final folds: `6c6758beed9fde198aaf75b7d3d5b5839ba292fa95b1acf2d08fb18689d124f3`
- final summary: `86a02044efc793af1b75c9e083eb19de894f1f30acda6e8db856d989f26ddab5`
- predictions: `15a506251a0a857a670cc81067090e1ade42a7b4293e77b3922efd101fce89b1`
- exact-day skill/count rows: `6c66076fc42cf694bc3c713910c4340c020a9856dfb2044b8bfa1cb1a95cea07`
- final coefficient/scaler description: `b6759100ce1b3de3a4b93111e6d083640240b7ae5d2587b5d63635616a078fe2`
- HPO decision: `c69f108e3589914a36ea9d4ecf7d1d1851d59bcbf20bdb37e4d1999cca08886e`
