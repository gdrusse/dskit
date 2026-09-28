## Decision

The selected exact-expiry variability model is a two-member, 16-channel TCN,
fitted separately for SPY, QQQ, and IWM. On each quote date it emits one row
for every observed expiry from one through 45 actual calendar days away. It
does not emit seven bucket diagnostics.

The model is a research champion, not a released production artifact. Recent
skill is concentrated in QQQ and is negative for SPY and IWM, so the result
does not justify trading.

## What one prediction means

For an index, after-close quote date, and exact expiry, the model forecasts

\[
\widehat V_{t,e}\approx
\sum_{s=t+1}^{S(e)}\left[\log(P_s/P_{s-1})\right]^2,
\]

the total close-to-close realized variance from the next session through the
last trading session on or before expiry. It is stored as
`integrated_variance_hat`.

For interpretation only, the output also includes

\[
\widehat\sigma_{daily}=\sqrt{\widehat V/N},\qquad
\widehat\sigma_{annual}=\sqrt{252}\,\widehat\sigma_{daily},
\]

where \(N\) is the actual number of remaining trading sessions. Those are
deterministic transforms of the same prediction, not additional learned
targets.

The output schema is:

| Column | Meaning |
|---|---|
| `symbol` | SPY, QQQ, or IWM |
| `quote_date` | after-close forecast timestamp |
| `expiry` | exact archive expiration date |
| `settlement_date` | last trading session on or before expiry |
| `calendar_dte` | actual calendar days from quote to settlement |
| `sessions_to_expiry` | actual intervening trading sessions |
| `integrated_variance_hat` | learned total-variance forecast |
| `rms_daily_hat` | square root of variance divided by sessions |
| `annualized_vol_hat` | daily RMS multiplied by square root of 252 |

For a concrete historical example, on 2025-11-12 for the 2025-12-12 expiry,
calendar DTE was 30 and the actual trading-session count was 21:

| Index | Integrated variance | Daily RMS | Annualized interpretation |
|---|---:|---:|---:|
| SPY | 0.001947 | 0.9628% | 15.28% |
| QQQ | 0.002775 | 1.1496% | 18.25% |
| IWM | 0.003407 | 1.2738% | 20.22% |

Thus a 30-day and 45-day expiry are different rows with their own exact
settlement and session counts. No 22-session substitute is used.

## What the model looks like

Each index model receives the same selected 49 columns:

- `ret_lag_0` through `ret_lag_21`;
- `rv_1`, `rv_5`, `rv_22`, `rv_66`, and index-specific `own_iv`;
- exact `calendar_dte`, `sessions_to_expiry`, both log transforms;
- archived series age and total tenor in calendar and trading units;
- calendar and session elapsed-life fractions and calendar-days per session;
- VIX, VIX9D, VIX3M, VIX6M, VIX1Y, VVIX, SKEW, and four tenor-minus-VIX
  spreads.

The 22 return columns form a chronological sequence. The other 27 columns are
static state and are broadcast along that sequence, producing 28 input
channels. Each ensemble member applies:

1. a causal width-two convolution, dilation one, from 28 to 16 channels;
2. ReLU;
3. a causal width-two convolution, dilation two, from 16 to 16 channels;
4. ReLU and a linear head from the last state to one standardized target.

The two convolution layers have a four-session receptive field. The model is
given the common 22-lag matrix for a fair zoo, but this TCN's forecast uses the
four most recent returns; the older 18 return lags are outside its receptive
field. All 27 static features remain visible because they are repeated at each
step.

Each seed has 1,457 trainable parameters; seeds 0 and 1 are trained separately
and averaged. Training uses 15 epochs, batch size 2,048, learning rate 0.001,
and weight decay 0.0001 on CUDA. Inputs and target are centered and scaled
from training rows only. The physical-unit prediction is

\[
\widehat V=\max\left(0,\mu_V+s_V\frac{f_0(X)+f_1(X)}{2}\right).
\]

The clamp is material: 8.99% of validation forecasts are exactly zero (SPY
11.13%, QQQ 7.69%, IWM 7.35%). A later positive head or log-variance target is
preferable before productionization.

## Training and validation procedure

The procedure has three distinct stages.

1. **Feature-family development, 2013-2018.** Ridge and LightGBM screened eight
   bundles on annual expanding folds. The selected 49-feature family was
   frozen before the architecture zoo.
2. **Architecture comparison, 2019-2025.** Eighteen models used identical
   features and rows in seven annual expanding folds for each index. The
   primary objective equally weighted every index/calendar-DTE cell.
3. **TCN HPO and gate.** Thirty-six TCN settings were searched on 2013-2018.
   The development winner doubled training to 30 epochs, but its later score
   was worse, so the 15-epoch zoo specification was retained.

Within every annual fold:

- features end at the quote close, so the timestamp is explicitly after-close;
- a training row is admitted only when its settlement target ends strictly
  before January 1 of the validation year;
- expiry must be fully covered by the underlying-price archive;
- missing inputs use training-fold medians;
- feature and target standardization use training rows only;
- the model is fit once, then forecasts every eligible validation row without
  refitting inside the year;
- negative physical-unit predictions are clamped to zero.

The empirical baseline is the expanding-training mean target conditioned on
the same exact calendar DTE. This prevents the model from receiving free skill
merely by knowing that a 45-day interval contains more variance than a 5-day
interval. The secondary baseline is trailing integrated variance over exactly
the same number of trading sessions as the future target.

The corrected 2019-2025 evaluation has 68,084 forecasts: 27,608 SPY, 21,493
QQQ, and 18,983 IWM. Architecture selection viewed this period, so it is not a
pristine family lockbox; future data are the next honest promotion test.

## Aggregate skill

Skill is `1 - model SSE / baseline SSE`; positive is better.

| Period | Index | Forecasts | Equal-day skill vs DTE mean | Pooled skill vs DTE mean | Pooled skill vs persistence | Positive days |
|---|---|---:|---:|---:|---:|---:|
| 2019-2025 | SPY | 27,608 | 18.06% | 14.33% | 32.35% | 42/45 |
| 2019-2025 | QQQ | 21,493 | 23.52% | 20.38% | 32.15% | 44/45 |
| 2019-2025 | IWM | 18,983 | 6.39% | 15.94% | 30.06% | 39/45 |
| 2022-2025 | SPY | 14,750 | -4.54% | -10.89% | 12.56% | 19/45 |
| 2022-2025 | QQQ | 14,593 | 26.55% | 27.03% | 20.27% | 43/45 |
| 2022-2025 | IWM | 13,263 | -20.71% | -24.96% | 11.30% | 8/45 |

Overall equal-index/day skill is 15.99%; pooled skill is 16.42% versus the DTE
mean and 31.58% versus matched persistence. Recent equal-index/day skill is
only 0.43%. QQQ is durable; recent SPY and IWM are not.

## Exact-day skill versus the DTE-conditioned mean, 2019-2025

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | -60.9% | -42.8% | -372.0% |
| 2 | 32.0% | 19.9% | -49.0% |
| 3 | 37.7% | 43.1% | 21.9% |
| 4 | 46.7% | 35.4% | 12.7% |
| 5 | 39.0% | 12.4% | -31.8% |
| 6 | 43.7% | 35.2% | 8.4% |
| 7 | 38.5% | 36.5% | 18.9% |
| 8 | 35.4% | 36.5% | 25.4% |
| 9 | 31.4% | 29.0% | 17.8% |
| 10 | 31.8% | 32.3% | 26.9% |
| 11 | 36.9% | 34.3% | 32.0% |
| 12 | 30.4% | 28.8% | -5.3% |
| 13 | 32.6% | 30.6% | -7.4% |
| 14 | 24.9% | 27.5% | 21.1% |
| 15 | 29.4% | 32.4% | 34.0% |
| 16 | 23.3% | 22.9% | 25.7% |
| 17 | 25.0% | 27.9% | 28.1% |
| 18 | 23.8% | 27.7% | 26.4% |
| 19 | 25.4% | 48.9% | 50.5% |
| 20 | 26.2% | 35.6% | 42.4% |
| 21 | 21.2% | 25.8% | 25.0% |
| 22 | 21.4% | 28.5% | 29.5% |
| 23 | 14.8% | 20.2% | 23.1% |
| 24 | 16.3% | 24.3% | 22.2% |
| 25 | 14.1% | 21.5% | 16.6% |
| 26 | 15.9% | 39.0% | 14.1% |
| 27 | 17.5% | 30.8% | 19.7% |
| 28 | 15.3% | 22.8% | 17.9% |
| 29 | 13.6% | 20.6% | 19.7% |
| 30 | 11.7% | 16.3% | 15.7% |
| 31 | 10.7% | 19.7% | 16.9% |
| 32 | 10.6% | 21.3% | 15.0% |
| 33 | 12.8% | 40.6% | 15.2% |
| 34 | 13.6% | 26.3% | 9.0% |
| 35 | 10.7% | 18.6% | 12.6% |
| 36 | 10.2% | 17.8% | 14.0% |
| 37 | 5.8% | 13.7% | 14.2% |
| 38 | 8.6% | 16.6% | 12.9% |
| 39 | 4.4% | 16.1% | 13.0% |
| 40 | -6.6% | 3.2% | -9.3% |
| 41 | 2.4% | 10.7% | 0.3% |
| 42 | 5.4% | 13.2% | 9.9% |
| 43 | 6.0% | 14.5% | 14.1% |
| 44 | -0.7% | 9.1% | 9.9% |
| 45 | 3.6% | 13.1% | 9.7% |

Coverage is uneven because real listings cluster at weekly/monthly expiries.
For example, DTE 40 has only 23/16/12 SPY/QQQ/IWM forecasts and DTE 41 has
40/31/27; their cell percentages are not reliable. Most shorter-DTE cells
have hundreds to more than one thousand observations.

## Exact-day skill versus matched-session persistence, 2019-2025

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | -9.2% | 7.7% | -106.6% |
| 2 | 34.5% | 21.8% | -56.7% |
| 3 | 19.3% | 53.1% | 2.7% |
| 4 | 39.1% | 53.7% | 37.9% |
| 5 | 12.8% | 54.9% | 38.4% |
| 6 | 29.2% | 41.9% | 20.7% |
| 7 | 25.1% | 25.5% | 4.9% |
| 8 | 15.9% | 31.4% | 27.6% |
| 9 | 15.7% | 24.0% | 11.3% |
| 10 | 24.4% | 41.8% | 29.3% |
| 11 | 31.6% | 47.7% | 45.7% |
| 12 | 22.9% | 51.6% | 56.6% |
| 13 | 34.5% | 50.9% | 51.6% |
| 14 | 25.5% | 41.8% | 32.8% |
| 15 | 30.5% | 39.2% | 40.5% |
| 16 | 26.9% | 32.0% | 34.1% |
| 17 | 18.5% | 31.0% | 23.5% |
| 18 | 19.4% | 23.1% | 18.3% |
| 19 | 16.0% | -40.2% | -57.9% |
| 20 | 24.3% | 11.3% | 26.9% |
| 21 | 21.9% | 13.4% | 15.7% |
| 22 | 27.9% | 27.3% | 28.3% |
| 23 | 28.8% | 28.0% | 30.1% |
| 24 | 29.8% | 35.3% | 32.2% |
| 25 | 30.0% | 31.9% | 29.8% |
| 26 | 24.0% | -27.6% | 1.8% |
| 27 | 25.5% | -45.0% | -19.9% |
| 28 | 31.3% | 20.3% | 21.6% |
| 29 | 32.8% | 27.2% | 30.5% |
| 30 | 39.4% | 32.8% | 35.0% |
| 31 | 34.6% | 40.7% | 36.2% |
| 32 | 39.3% | 43.9% | 39.9% |
| 33 | 30.1% | -17.6% | 9.2% |
| 34 | 33.7% | -14.0% | 4.8% |
| 35 | 35.2% | 22.1% | 22.0% |
| 36 | 38.7% | 32.7% | 33.5% |
| 37 | 38.1% | 38.4% | 37.0% |
| 38 | 33.7% | 39.8% | 34.3% |
| 39 | 52.1% | 45.0% | 40.0% |
| 40 | -2.1% | 1.0% | 0.3% |
| 41 | 0.8% | 5.3% | 3.6% |
| 42 | 29.3% | 31.5% | 26.4% |
| 43 | 43.9% | 43.7% | 41.2% |
| 44 | 32.4% | 35.1% | 32.8% |
| 45 | 33.9% | 37.4% | 28.3% |

## Recent exact-day skill versus the DTE mean, 2022-2025

| DTE | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 1 | -12.5% | -9.1% | -36.3% |
| 2 | 38.1% | 29.9% | 8.0% |
| 3 | 61.2% | 58.4% | 16.8% |
| 4 | 46.7% | 30.3% | -19.1% |
| 5 | 19.5% | 18.8% | -11.5% |
| 6 | 27.9% | 34.2% | -0.4% |
| 7 | 28.4% | 32.6% | -19.7% |
| 8 | 9.2% | 23.4% | -30.9% |
| 9 | 8.0% | 24.4% | -40.5% |
| 10 | 19.2% | 32.3% | -26.0% |
| 11 | 28.0% | 26.9% | -6.0% |
| 12 | 7.6% | 24.0% | -9.1% |
| 13 | 17.2% | 27.4% | -16.3% |
| 14 | -3.3% | 22.9% | -25.2% |
| 15 | 2.4% | 16.5% | -5.2% |
| 16 | -8.6% | 20.0% | 1.1% |
| 17 | 14.8% | 31.8% | -13.7% |
| 18 | -30.5% | 26.8% | -67.4% |
| 19 | -24.3% | 31.7% | -51.2% |
| 20 | -0.0% | 27.3% | -32.7% |
| 21 | 5.4% | 32.0% | -6.1% |
| 22 | 16.2% | 34.0% | 14.8% |
| 23 | -36.8% | 24.1% | -6.2% |
| 24 | -11.1% | 33.1% | -28.2% |
| 25 | -48.4% | 29.1% | -73.8% |
| 26 | -36.8% | 42.6% | -26.7% |
| 27 | -9.5% | 31.4% | -55.5% |
| 28 | -2.1% | 32.8% | -11.2% |
| 29 | 4.4% | 30.1% | 8.4% |
| 30 | -19.3% | 23.2% | -12.1% |
| 31 | -25.0% | 29.1% | -41.8% |
| 32 | -49.1% | 30.3% | -65.1% |
| 33 | -41.0% | 38.7% | -19.9% |
| 34 | -26.1% | 30.6% | -54.7% |
| 35 | -15.5% | 31.4% | -15.6% |
| 36 | -0.5% | 28.4% | 2.7% |
| 37 | -25.2% | 17.3% | -22.0% |
| 38 | -9.7% | 29.3% | -37.9% |
| 39 | -43.7% | 20.7% | -79.4% |
| 40 | 3.0% | -2.4% | 88.2% |
| 41 | 18.9% | 20.4% | 56.1% |
| 42 | -8.1% | 22.8% | -32.9% |
| 43 | -7.6% | 21.9% | -12.3% |
| 44 | -56.4% | 9.6% | -45.4% |
| 45 | -29.5% | 23.7% | -70.3% |

The 40-41 DTE recent values are dominated by tiny samples and should not be
interpreted as stable edge. The robust message is broader: QQQ retained skill
across 43 of 45 days, while recent SPY and IWM did not.

## What exists today

The local evidence includes fold metrics, 68,084 out-of-sample prediction rows,
the exact-day grids, output examples, feature identities, and model anatomy.
`final_tcn_predictions.parquet` is research evidence, not a deployable model.
There is no released serialized TCN, data-freshness contract, distribution
head, or option-entry node.

Before promotion, the model needs a positive scale specification, a truly new
forward fold, and a calibrated distribution conditional on the scale forecast.
Only after that should an option payoff or entry rule be evaluated.

## Evidence

Ignored local evidence is under
`children/index_options/pipeline_runs/exact_maturity_20260928/`:

- final folds: `05b50a20e726ec39d705c2b299a941e3cac934f5199065696a942656560fc592`
- final summary: `7543c69451f551d5e24f7f8cb673d53e8d32a7f136c8cd96439002ad50d8bb29`
- exact-day long grid: `37bdb0f8837fbdd9d71a5539f09979ece8bb3c7578f05a6116abee803d26b4b4`
- model anatomy: `c9a2dfeaf1958facbc37bef1d45b4957676796df17c9701451f9593ee25ebb36`
- HPO candidate summary: `1e9d0ad47d79d4ffbc3b27ab6da5b842fd3ea65049639e596d719e4aa3237d70`
- final stability decision: `aea21e4d1b0ab20c94c5286618226331a54868fd134d1bd3c113c9f29b10cfc5`
