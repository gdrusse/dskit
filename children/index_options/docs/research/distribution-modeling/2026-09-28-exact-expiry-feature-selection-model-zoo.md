## Correction and decision

The learned target is **daily RMS realized volatility measured over the actual
quote-to-expiry trading sessions**, with exact expiry retained on every row.
The final architecture is ElasticNet on 38 causal features. Endpoint-inclusive
HPO selected `alpha=0.03`, `l1_ratio=0.75`.

This corrects the first version of this memo, which trained one cross-maturity
model per index directly on raw integrated variance. That formulation made a
linear model approximate the multiplicative relationship
`variance rate × sessions` with additive terms. It produced implausibly bad
one- to six-day Ridge scores and incorrectly selected TCN.

The corrected result is coherent: TCN, ElasticNet, and Ridge score 40.25%,
40.08%, and 39.98% respectively in the fixed zoo; tuned ElasticNet scores
40.37%. All three fixed models beat the exact-DTE mean in 133 of 135
index/day cells. The earlier raw-variance TCN selection is superseded.

This remains variability modeling, not a distribution model, option-entry
rule, backtest, or authorization to trade.

## Why the first linear result was wrong

For actual expiry (e) with (N_{t,e}) remaining trading sessions, integrated
variance is

```math
V_{t,e}=\sum_{s=t+1}^{S(e)}r_s^2
=N_{t,e}\,\sigma_{t,e}^2,
\qquad
\sigma_{t,e}=\sqrt{V_{t,e}/N_{t,e}}.
```

The first run fitted one model per index directly to (V). A linear additive
model cannot naturally express `state-dependent variance rate × horizon`.
Raw MSE also gives long horizons and volatile regimes much larger absolute
losses, even though the registered evaluation equally weighted exact-DTE
cells. Ridge therefore improved pooled raw-variance MSE by 11.73% but had
-37.58% equal-cell skill. Its worst cells were not tiny samples:

| Cell | Observations | Ridge MSE / baseline MSE |
|---|---:|---:|
| IWM 1 DTE | 729 | 23.75× |
| SPY 1 DTE | 1,003 | 12.51× |
| QQQ 1 DTE | 886 | 9.51× |
| IWM 5 DTE | 505 | 6.80× |

An immediate controlled diagnostic kept the same rows and features but fitted
Ridge to exact-window daily RMS, then derived integrated variance as
`RMS² × actual sessions`. Skill changed from -37.58% to +25.85%, with 133 of
135 cells positive. That isolated target geometry as the cause; Ridge code,
the archive, and the expiry calendar were not the failure.

The full feature selection, 18-model zoo, and HPO were then rerun on daily RMS.
The exact expiry, settlement date, calendar DTE, and trading-session count are
still present on every prediction row. Nothing is approximated to 22 sessions.

## Data and exact-expiry panel

The archive scan read 53,407,120 option-contract rows from 72 files in 31.86
seconds. It produced 290,131 distinct quote-date/expiry observations across
5,182 symbol-expiry series. An expiry series is one index and one expiration
date observed on all archived quote dates.

Rows are admitted only when the full underlying path through settlement is
available:

| Index | Complete modeling rows | First quote | Last complete quote |
|---|---:|---:|---:|
| SPY | 44,770 | 2008-01-02 | 2025-12-11 |
| QQQ | 32,639 | 2011-03-23 | 2025-12-12 |
| IWM | 31,946 | 2008-01-02 | 2025-12-12 |

The target is

```math
y_{i,t,e}=\sqrt{\frac{1}{N_{t,e}}
\sum_{s=t+1}^{S(e)}\left[\log(P_{i,s}/P_{i,s-1})\right]^2}.
```

It is daily log-return RMS over that row's actual expiry window. Calendar DTE
is the public grid key; (N) comes from the trading calendar. The learned
prediction can be transformed without approximation into integrated variance,
`y_hat² × N`, or annualized volatility, `y_hat × sqrt(252)`.

All same-date market inputs make this an after-close forecast. A right-edge QA
refused 443 late-2025 rows whose settlement windows exceeded the available
price archive.

## Candidate feature space

The 104-column full space contained:

- 22 close-to-close return lags and realized volatility over 1, 5, 22, and 66
  sessions;
- VIX for SPY, VXN for QQQ, or RVX for IWM;
- exact calendar/session DTE, log transforms, first-seen series age, total
  tenor, elapsed-life fractions, and calendar-days per remaining session;
- quote and expiry seasonality, standard-monthly status, month/quarter-end
  distance, and adjacent trading-calendar gaps;
- same-date option-surface IV, 25-delta skew, spread, open interest, volume,
  depth, contract-count, and put/call aggregates;
- downside/upside variance, momentum, drawdown, and volatility-of-volatility;
- VIX term structure, VVIX, SKEW, and cross-asset volatility indices.

Series age begins at the first archived quote because the source has no
authoritative exchange listing timestamp. It is a causal first-seen proxy, not
the true option opening date.

## Corrected feature selection

Feature-family selection used expanding annual folds from 2013 through 2018.
Ridge and LightGBM tested eight bundles on the same rows. A training row was
eligible only when its target ended before the validation year. Missing values
used training-fold medians; scaling used training rows only.

There were 288 fits: eight bundles × two selectors × three indexes × six
years. Across the 18 development index-year folds there were 33,528 validation
predictions per bundle. Fold training size ranged from 1,312 rows (QQQ 2013)
to 12,959 rows (SPY 2018); the companion output memo gives every fold's exact
training/validation count. The smallest bundle within 0.5% of the best
combined selector score was selected:

| Rank | Bundle | Features | Validation rows | Relative MSE | Skill |
|---:|---|---:|---:|---:|---:|
| 1 | **core maturity** | **38** | **33,528** | **0.48241** | **51.76%** |
| 2 | core + option surface | 51 | 33,528 | 0.49673 | 50.33% |
| 3 | HAR + maturity | 16 | 33,528 | 0.50162 | 49.84% |
| 4 | core + asymmetry | 52 | 33,528 | 0.51592 | 48.41% |
| 5 | core + cross-asset volatility | 48 | 33,528 | 0.52789 | 47.21% |
| 6 | core + VIX term/tail | 49 | 33,528 | 0.54540 | 45.46% |
| 7 | core + seasonality | 56 | 33,528 | 0.56143 | 43.86% |
| 8 | full | 104 | 33,528 | 0.66215 | 33.78% |

The selected 38 columns are 22 return lags; `rv_1`, `rv_5`, `rv_22`, `rv_66`;
index-specific `own_iv`; and 11 exact-maturity/lifecycle columns. The option
surface and seasonality were tested but did not add enough development skill.

## Corrected 18-model zoo

The zoo used expanding annual validation folds from 2019 through 2025. Every
model received the same 38 columns, 68,084 validation rows, targets, baselines,
and embargo. Each model had 21 index-year fits. Torch models used two seeds,
15 epochs, train-only standardization, and the RTX 5060 Ti. Fold training size
ranged from 11,055 rows (QQQ 2019) to 41,317 rows (SPY 2025).

The primary score equally weights all 135 index/exact-DTE cells:

| Rank | Model | Validation rows | Relative MSE | Skill vs DTE mean | Recent skill | Positive cells |
|---:|---|---:|---:|---:|---:|---:|
| 1 | TCN | 68,084 | 0.59748 | 40.25% | 36.86% | 133/135 |
| 2 | **ElasticNet** | 68,084 | 0.59916 | 40.08% | 37.70% | 133/135 |
| 3 | Ridge | 68,084 | 0.60017 | 39.98% | 37.54% | 133/135 |
| 4 | PatchTST | 68,084 | 0.61106 | 38.89% | 35.54% | 133/135 |
| 5 | CNN1D | 68,084 | 0.61496 | 38.50% | 36.88% | 132/135 |
| 6 | MLP | 68,084 | 0.62051 | 37.95% | 35.06% | 133/135 |
| 7 | Extra Trees | 68,084 | 0.65283 | 34.72% | 27.85% | 132/135 |
| 8 | GRU-attention | 68,084 | 0.65750 | 34.25% | 37.71% | 133/135 |
| 9 | GRU | 68,084 | 0.65784 | 34.22% | 37.95% | 133/135 |
| 10 | TFT-lite | 68,084 | 0.65950 | 34.05% | 34.71% | 133/135 |
| 11 | LSTM | 68,084 | 0.66000 | 34.00% | 34.00% | 133/135 |
| 12 | LightGBM | 68,084 | 0.66107 | 33.89% | 24.66% | 132/135 |
| 13 | Transformer | 68,084 | 0.67041 | 32.96% | 32.44% | 133/135 |
| 14 | random forest | 68,084 | 0.67423 | 32.58% | 20.46% | 132/135 |
| 15 | histogram GBR | 68,084 | 0.67881 | 32.12% | 21.57% | 132/135 |
| 16 | LSTM-attention | 68,084 | 0.68398 | 31.60% | 34.17% | 131/135 |
| 17 | NLinear | 68,084 | 0.86225 | 13.78% | -8.74% | 121/135 |
| 18 | DLinear | 68,084 | 0.88108 | 11.89% | -4.44% | 121/135 |

TCN's point advantage over fixed ElasticNet is 0.168 percentage points of
equal-cell skill. ElasticNet and Ridge are stronger recently. MLP remains a
respectable sixth-place benchmark rather than the winner.

## Endpoint-inclusive ElasticNet HPO

The corrected HPO used only the 2013-2018 development folds:

- 10 alpha values from 0.00001 through 0.1;
- nine L1 ratios: 0, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 1;
- 90 candidates × 18 index-year folds = 1,620 fits;
- 33,528 out-of-sample development predictions for every candidate;
- exact Ridge at ratio 0, exact Lasso at ratio 1, and interior ElasticNet;
- primary objective: equal-index/exact-DTE relative MSE.

The solver used the standardized ElasticNet objective directly. Ridge was
closed form; Lasso/interior solutions used deterministic FISTA and every fit
passed an explicit KKT residual threshold of `1e-5` (maximum observed
`9.997e-6`). This replaced an initial coordinate-descent attempt that did not
converge for the weakest penalties.

The development winner was `alpha=0.03`, `l1_ratio=0.75`, with 54.53% skill
and all 135 development cells positive. Locked on the later folds, it achieved:

- **40.37%** equal-index/day skill;
- **42.24%** pooled skill versus the exact-DTE mean;
- **35.19%** pooled skill versus matched-session persistence;
- 133 of 135 cells positive;
- **38.93%** equal-index/day skill in 2022-2025.

Tuned ElasticNet therefore narrowly exceeds the fixed TCN overall and is 2.07
percentage points stronger recently. It is selected as the final research
architecture because it wins the registered score after development-only HPO,
is simpler, and exposes its coefficients directly.

The 2019-2025 period is not a pristine architecture lockbox because the zoo
viewed it. HPO values were selected on 2013-2018, but the next true promotion
test is future data.

## Evidence

Corrected ignored evidence is under
`children/index_options/pipeline_runs/exact_maturity_rms_20260928/`:

- protocol: `dda6da89b2ca74d9112e8196555d8f0385c26d99a1bcb207e0e2a66da6904253`
- feature decision: `9d0a7d6ecc7dfd0f10d2cfd755bc67fd50b0bc7fc6957bf5632dae896e4f13e2`
- 288 feature folds: `c6df1f45125d2b59388645ac36811b8fe9dabcd511fbacfb2898a182249129b2`
- corrected zoo folds: `d8704073bb5d26235ba7bcdc050f2069095c1c8ce7cc542d9044f8230c517972`
- corrected zoo summary: `7f8b6a7f068314e9b67ba42addd60bded7c9aac0a575bb931161f6f198dbd4b5`
- HPO decision: `c69f108e3589914a36ea9d4ecf7d1d1851d59bcbf20bdb37e4d1999cca08886e`
- 1,620 HPO folds: `4774d3b1c7698bb5474dd897166411353b5a037e040234002e6ba8c04f077bd2`

The raw-integrated-variance artifacts remain locally as diagnostic evidence of
the rejected formulation. They must not be used as final model evidence.
