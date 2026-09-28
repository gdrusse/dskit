## Decision

Replace the seven fixed forward-session diagnostics with an **exact-expiry
integrated-variance model**. The research champion is a small temporal
convolutional network (TCN) using 49 causal features: recent returns, HAR-style
realized volatility, exact maturity and lifecycle state, own implied volatility,
and the VIX term/tail complex.

This is a variability model, not a trading model. It does not estimate return
direction, option P&L, a condor entry, or a full return distribution. The recent
result is fragile: aggregate 2022-2025 equal-index/day skill is only 0.43%.

## Question

For every SPY, QQQ, and IWM option expiry one to 45 calendar days away, which
causal feature family and model architecture best predict total realized
variance from the quote close through that expiry's settlement session?

## Exact-expiry panel

The archive scan read 53,407,120 contract rows from 72 files in 31.86 seconds.
It found 290,131 distinct quote-date/expiry observations and 5,182 expiry
series. An expiry series means one symbol-expiration pair, such as SPY options
expiring 2024-06-21, observed on all archived quote dates.

The modeling panel retains exact calendar DTE 1 through 45 and requires a
complete underlying-price path through settlement:

| Index | Modeling rows | First quote | Last complete quote |
|---|---:|---:|---:|
| SPY | 44,770 | 2008-01-02 | 2025-12-11 |
| QQQ | 32,639 | 2011-03-23 | 2025-12-12 |
| IWM | 31,946 | 2008-01-02 | 2025-12-12 |

The target for index (i), quote date (t), and actual expiry (e) is

\[
V_{i,t,e}=\sum_{s=t+1}^{S(e)}
\left[\log(P_{i,s}/P_{i,s-1})\right]^2,
\]

where \(S(e)\) is the last trading session on or before the stored expiry.
This is integrated realized variance, not a 22-session approximation and not
an annualized volatility. Calendar DTE remains the public grid key;
sessions-to-expiry comes from the actual trading calendar and is an input.

A right-edge QA pass found 443 late-2025 rows whose expiries extended past the
price archive. They were removed and every affected zoo and final evaluation
was rerun. No reported forecast has a censored settlement window.

## Feature space

The candidate space included 104 columns in seven substantive families:

- 22 close-to-close return lags and trailing realized volatility over 1, 5,
  22, and 66 sessions;
- own implied volatility: VIX for SPY, VXN for QQQ, and RVX for IWM;
- exact maturity and lifecycle: calendar and session DTE, log transforms,
  elapsed calendar/trading age, total listed tenor, elapsed-life fractions,
  and calendar-days per remaining session;
- quote and expiry weekday/month/day-of-year seasonality, standard-monthly
  status, month/quarter-end distance, and adjacent trading-calendar gaps;
- same-date option-surface aggregates: mean and ATM IV, 25-delta call/put IV,
  risk reversal, relative spread, contract count, open interest, volume,
  quote depth, and put/call ratios;
- downside/upside variance, momentum, drawdown, and volatility-of-volatility;
- VIX term structure, VVIX, SKEW, and gold/oil/emerging-market/silver/Treasury
  volatility indices and their spreads to VIX.

The lifecycle origin is the option's **first archived quote**, because the
source has no authoritative listing timestamp. It is a causal first-seen
proxy, not a claim about the exchange's true opening date. All same-date market
features make this an after-close forecast.

## Feature selection

Feature-family selection used annual expanding folds from 2013 through 2018.
Ridge and LightGBM evaluated each bundle on the same rows. Training rows were
admitted only when their complete target ended before the validation year.
Missing values were replaced by training-fold medians; scaling used training
rows only. The primary objective was the mean, equally across all 135
index/exact-DTE cells, of model MSE divided by an expanding-training mean
conditioned on that exact calendar DTE.

There were 288 selector fits: eight bundles, two selector models, three
indexes, and six validation years. The registered rule chose the smallest
bundle within 0.5% of the best combined selector score:

| Rank | Bundle | Features | Relative MSE |
|---:|---|---:|---:|
| 1 | core + VIX term/tail | 49 | **1.00346** |
| 2 | core + option surface | 51 | 1.05823 |
| 3 | HAR + maturity | 16 | 1.09616 |
| 4 | core maturity | 38 | 1.39296 |
| 5 | core + cross-asset vol | 48 | 1.57200 |
| 6 | core + asymmetry | 52 | 1.66764 |
| 7 | core + seasonality | 56 | 1.69077 |
| 8 | full | 104 | 1.80655 |

The winning 49 columns are the 22 returns; `rv_1`, `rv_5`, `rv_22`, `rv_66`;
`own_iv`; 11 maturity/lifecycle columns; VIX, VIX9D, VIX3M, VIX6M, VIX1Y,
VVIX, SKEW; and four VIX tenor spreads.

The selector score of 1.00346 is slightly worse than its empirical baseline.
Therefore feature selection says only that this was the least-bad stable
family for Ridge/LightGBM on development folds. It does not itself establish
skill. The option-surface and seasonality data were genuinely tested and
rejected under this screen; they were not silently omitted.

## Controlled 18-model zoo

The architecture zoo used annual expanding folds from 2019 through 2025. All
18 models received the same selected columns, label rows, target, baselines,
and embargo. The 12 Torch architectures used two deterministic seeds, 15
epochs, standardized training-only features and target, and the RTX 5060 Ti.
Each model produced 68,084 forecasts over 21 index-year folds.

The primary equal-index/exact-DTE ranking was:

| Rank | Model | Relative MSE | Skill vs DTE mean | Recent skill, 2022-2025 |
|---:|---|---:|---:|---:|
| 1 | **TCN** | **0.84009** | **15.99%** | 0.43% |
| 2 | TFT-lite | 0.85762 | 14.24% | 3.56% |
| 3 | histogram GBR | 0.86895 | 13.11% | -18.73% |
| 4 | LSTM-attention | 0.88241 | 11.76% | 3.05% |
| 5 | LightGBM | 0.88657 | 11.34% | -18.63% |
| 6 | GRU | 0.88918 | 11.08% | -1.91% |
| 7 | LSTM | 0.89572 | 10.43% | -2.42% |
| 8 | Transformer | 0.89638 | 10.36% | 0.71% |
| 9 | GRU-attention | 0.93556 | 6.44% | -9.80% |
| 10 | MLP | 0.94472 | 5.53% | -28.43% |
| 11 | CNN1D | 0.94527 | 5.47% | -8.14% |
| 12 | PatchTST | 0.96697 | 3.30% | -14.51% |
| 13 | Extra Trees | 0.99922 | 0.08% | -8.38% |
| 14 | random forest | 1.14255 | -14.25% | -5.07% |
| 15 | ElasticNet | 1.36511 | -36.51% | -73.65% |
| 16 | Ridge | 1.37577 | -37.58% | -76.31% |
| 17 | DLinear | 1.71743 | -71.74% | -83.97% |
| 18 | NLinear | 1.80486 | -80.49% | -107.50% |

TCN also has 16.42% pooled skill versus the exact-DTE mean, 31.58% pooled
skill versus matched-session trailing variance, and positive mean-baseline
skill in 125 of 135 index/day cells. TFT-lite wins more cells, 127 of 135, and
has the stronger recent score, but its registered primary loss is worse.

MLP was retained as requested. It is positive over the full period but sharply
negative recently. Linear models that were strong on the previous fixed
horizon/RMS-volatility formulation fail on this raw integrated-variance target;
the earlier ElasticNet result is not transferable to this different label.

## TCN hyperparameter search and stability gate

The development-only HPO searched 36 configurations:

- hidden channels: 8, 16, 32;
- epochs: 15, 30;
- learning rate: 0.0003, 0.001, 0.003;
- weight decay: 0 or 0.0001;
- batch size 2,048 and seeds 0/1 fixed.

That is 648 fits over the 18 index-year development folds. The development
winner was 16 channels, 30 epochs, learning rate 0.001, and weight decay
0.0001. Its 2013-2018 equal-index/day skill was 48.48%, versus 40.11% for the
15-epoch default.

The locked later comparison rejected that apparent improvement. The 30-epoch
candidate scored 14.23% over 2019-2025 and -8.03% over 2022-2025, versus
15.99% and +0.43% for the simpler 15-epoch TCN. The final research
specification therefore retains 16 channels, 15 epochs, learning rate 0.001,
weight decay 0.0001, batch size 2,048, and the two-seed average.

This stability gate is evidence that extra development fit did not generalize.
It is not a pristine model-family lockbox: the 2019-2025 period was used to
rank architectures and to reject the HPO candidate. A genuinely new future
fold is required before promoting this model.

## Interpretation

There is real but regime-dependent skill in modeling exact-expiry integrated
variance. QQQ is the most stable index; SPY and especially IWM deteriorate in
2022-2025. The useful result is a ranked variability model and a causal target
definition, not readiness to trade.

The next modeling experiment should use a positive target transform or
positive output head—roughly 9.0% of final TCN forecasts hit the zero clamp—and
should judge TFT-lite and TCN on new data. Option-entry research should wait
for a calibrated distribution around this scale forecast.

## Evidence

Ignored local evidence is under
`children/index_options/pipeline_runs/exact_maturity_20260928/`:

- archive extraction: `d85cdc591d99cf35946e1c0f3a364c313fe190c3d63bd596e0041f929c6e85af`
- exact-expiry surface: `3f61401743995605722740e43520c2f9402009251f2b26afb689fe5b8805a8d6`
- feature decision: `018ab40bdf9eb3943a9dd2bb52a3e08b674888c7100500efed8b5342b43037f3`
- 288 feature folds: `1c194c1334d73f025912650039542c4a40f210bc31d079acf12efb9a9ec9b753`
- corrected zoo folds: `b21e8dde286af8298d135a0ee616cf5907ae92affb627caae876f2174c7e5e0a`
- corrected zoo summary: `1e63946edc29946846486eb4f6f6a6b960418d3cbada32bedfbb5599cedbfb`
- 648 HPO folds: `9376a7bf8bdc8f606285ebc985c95572a0620805b621cdd772a365f399c79eb1`
- final decision: `aea21e4d1b0ab20c94c5286618226331a54868fd134d1bd3c113c9f29b10cfc5`

This memo supersedes the seven fixed-horizon model choice for exact option
expiry work. The prior reports remain valid descriptions of their different
fixed-session RMS-volatility experiment.
