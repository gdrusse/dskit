## Decision

The final variability-model architecture is **ElasticNet**, with one separately
fitted model for each of the 21 SPY/QQQ/IWM horizon cells. A pre-2019 global
hyperparameter search selected an interior mix, `alpha=0.03` and
`l1_ratio=0.5`. The search included exact Ridge (`l1_ratio=0`) and exact Lasso
(`l1_ratio=1`) endpoints; neither endpoint won.

This model forecasts realized volatility. It is not a return-direction model,
an option-distribution model, an option-pricing model, or a trading rule.

## What one model actually outputs

For underlying (i), forecast date (t), and horizon (h), the target is

```math
y_{i,t,h} = \sqrt{\frac{1}{h}\sum_{k=1}^{h} r_{i,t+k}^{2}},
\qquad
r_{i,t}=\log(P_{i,t}/P_{i,t-1}).
```

The model outputs one nonnegative scalar, `rv_hat`, in **daily log-return
volatility units**. If `rv_hat=0.012`, the forecast says the next horizon is
expected to realize about 1.2% root-mean-square movement per session. For rough
interpretation, that is about 19.0% annualized (`0.012 * sqrt(252)`); over a
10-session horizon its standard-deviation scale is approximately
`0.012 * sqrt(10)`. Those conversions are interpretations, not additional model
outputs.

Running all cells on one date produces 21 scalars: seven for SPY, seven for
QQQ, and seven for IWM. The bucket-to-target mapping is fixed:

| Option bucket | 1 | 2-3 | 5 | 7-10 | 14 | 21 | 30-45 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Forecast sessions | 1 | 2 | 3 | 5 | 10 | 15 | 22 |

The model does **not** output a call/put probability, return sign, quantile,
expected option P&L, condor entry, or confidence interval. It supplies only the
conditional scale forecast that a later distribution model could consume.

## What the model looks like

Every cell uses the same 27 columns:

- `ret_lag_0` through `ret_lag_21`: the most recent 22 close-to-close log
  returns, with lag 0 the return ending on the forecast date;
- `rv_1`, `rv_5`, `rv_22`, `rv_66`: trailing root-mean-square realized
  volatility over 1, 5, 22, and 66 sessions;
- `own_iv`: VIX for SPY, VXN for QQQ, and RVX for IWM, observed on the forecast
  date.

All input means and standard deviations, plus the target mean and standard
deviation, are fitted on the training rows only. The prediction is

```math
\widehat y = \max\left(0,\; \mu_y + s_y\left[b +
\sum_{j=1}^{27}\beta_j\frac{x_j-\mu_j}{s_j}\right]\right).
```

ElasticNet fits the standardized coefficients by minimizing squared error plus
an equal L1/L2 penalty:

```math
\frac{1}{2n}\lVert z_y-X_z\beta\rVert_2^2
+0.03\left(0.5\lVert\beta\rVert_1
+0.25\lVert\beta\rVert_2^2\right).
```

This is a regularized linear model. The L1 half sets weak coefficients exactly
to zero; the L2 half stabilizes correlated return lags and volatility windows.
The 21 full-history explanatory refits retain 9 to 22 of 27 inputs, averaging
16.6 nonzero coefficients.

The fitted structure is consistent across cells:

- `own_iv` is the largest standardized coefficient in all 21 models;
- `rv_5` and `ret_lag_1` are among the five largest in all 21;
- `rv_22` is among the five largest in 18 of 21;
- recent return coefficients are usually negative, so a negative recent return
  raises the volatility forecast, the familiar leverage/asymmetry effect;
- longer-horizon cells are sparser: QQQ 30-45 retains 9 inputs and IWM 30-45
  retains 10, versus 19-22 for many one-session models.

For the SPY one-session explanatory refit, the five largest standardized
coefficients are `own_iv +0.354`, `rv_5 +0.125`, `ret_lag_1 -0.083`,
`ret_lag_3 -0.074`, and `ret_lag_0 -0.067`. These standardized values describe
relative influence; production evaluation applies the cell's saved train means
and scales before the physical-unit forecast is reconstructed.

These explanatory refits use all usable labeled history through December 2025:
about 6,482-6,503 rows per SPY cell, 4,068-4,089 per QQQ cell, and 4,061-4,082
per IWM cell. They document the model shape; validation skill below comes only
from forecasts made outside each fold's training window.

## Hyperparameter selection

The global search was completed before evaluating the locked parameter choice
on 2019-2025:

- development validation years: 2013-2018;
- cells: all 21;
- alpha grid: `0.00001, 0.00003, 0.0001, 0.0003, 0.001, 0.002, 0.003,
  0.01, 0.03, 0.1`;
- L1-ratio grid: `0, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 1`;
- candidates: 90;
- fits per candidate: 21 cells times 6 annual folds = 126;
- total development fits: 11,340;
- objective: mean across cells of validation MSE divided by the corresponding
  expanding-train mean forecast's MSE.

The Ridge endpoint was implemented exactly as
`Ridge(alpha=n_train*elastic_alpha)` so its objective matched ElasticNet's
scaling. The Lasso endpoint used `Lasso(alpha=elastic_alpha)`. The winner was
the true interior pair `alpha=0.03`, `l1_ratio=0.5`, with development
equal-cell relative MSE 0.50649.

A second sensitivity run re-tuned inside every later fold using the preceding
three years. It chose Ridge in 44 folds, Lasso in 53, and an interior mix in 50,
but its total validation error was slightly worse than the globally locked
pair. This instability is why the final specification uses one pre-2019 global
pair rather than changing regularization from fold to fold.

## Training and validation procedure

For every annual fold and every cell:

1. Build features using data ending on date (t). `own_iv` is the same-date
   closing volatility index, so the forecast decision is an **after-close**
   forecast, not one available earlier that day.
2. Build the forward target from the next (h) close-to-close returns.
3. Admit a training row only if its target-end date is strictly before January
   1 of the validation year. This is the horizon embargo: no training label can
   overlap validation.
4. Fit feature and target standardization on training rows only.
5. Fit the ElasticNet on all eligible expanding-history training rows.
6. Freeze the scaler and coefficients, forecast every eligible session in the
   validation calendar year, clamp a negative scale forecast to zero, and score
   those forecasts without refitting.

The locked evaluation uses seven annual validation folds, 2019 through 2025,
for each of 21 cells: 147 cell-year folds and 36,527 validation predictions.
The locked pair has:

- pooled skill versus expanding-train mean: **42.47%**;
- pooled skill versus trailing-volatility persistence: **39.42%**;
- equal-cell average skill versus mean: **43.87%**;
- positive skill in all 21 cells;
- pooled 2022-2025 skill versus mean: **31.44%**.

Compared with the original fixed ElasticNet (`alpha=0.002`, `l1_ratio=0.1`),
the locked HPO pair reduces pooled MSE by 0.10%; versus the fixed Ridge it
reduces pooled MSE by 0.12%. Cluster-bootstrap intervals include zero, so the
HPO gain is small; the important result is that the endpoint-inclusive search
selected a genuine mixture without degrading the architecture.

Because ElasticNet was chosen after viewing the earlier 2019-2025 architecture
zoo, this period is locked with respect to the HPO values but is **not** a
pristine model-family lockbox. The next genuinely new evidence is a future
fold not used in either architecture or hyperparameter selection.

## Skill by cell

Skill is `1 - model MSE / baseline MSE`; positive values mean improvement. Each
cell below pools its seven annual 2019-2025 validation folds.

### Skill versus expanding-train mean

| Index | 1 | 2-3 | 5 | 7-10 | 14 | 21 | 30-45 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SPY | 33.39% | 49.92% | 54.90% | 56.48% | 50.54% | 42.50% | 33.10% |
| QQQ | 26.89% | 42.62% | 49.70% | 54.62% | 52.16% | 47.40% | 41.65% |
| IWM | 24.55% | 38.58% | 44.86% | 48.73% | 47.63% | 43.41% | 37.74% |

The strongest mean-baseline cells are SPY 7-10 (56.48%), SPY 5 (54.90%), QQQ
7-10 (54.62%), and QQQ 14 (52.16%). The weakest are IWM 1 (24.55%), QQQ 1
(26.89%), and SPY 30-45 (33.10%). The signal is therefore clearest at middle
horizons and weakest for noisy one-session volatility, especially IWM.

### Skill versus trailing-volatility persistence

| Index | 1 | 2-3 | 5 | 7-10 | 14 | 21 | 30-45 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SPY | 47.60% | 39.59% | 34.52% | 30.23% | 32.47% | 35.12% | 38.43% |
| QQQ | 47.63% | 42.85% | 39.46% | 32.47% | 32.31% | 33.07% | 36.28% |
| IWM | 47.82% | 37.99% | 35.21% | 32.96% | 30.98% | 30.75% | 33.43% |

The persistence comparison reverses some of the horizon pattern because
one-session trailing absolute return is a particularly weak standalone
forecast. Every cell still improves materially on persistence.

### Recent-period skill versus expanding-train mean, 2022-2025

| Index | 1 | 2-3 | 5 | 7-10 | 14 | 21 | 30-45 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SPY | 18.05% | 32.45% | 42.55% | 47.05% | 42.81% | 39.60% | 40.21% |
| QQQ | 19.20% | 33.82% | 43.56% | 51.20% | 52.81% | 50.33% | 49.50% |
| IWM | 7.16% | 13.28% | 19.45% | 24.84% | 26.20% | 27.40% | 31.49% |

The main caution is IWM in the recent regime, especially at one and two
sessions. Its skill remains positive but is much smaller. QQQ's medium and long
horizons are the most stable recent cells.

## What is and is not tangible today

The research output now includes a complete coefficient/scaler description for
21 explanatory refits and fold-level skill evidence. It does not yet include a
released serialized pipeline model, a distribution head, or an option-entry
artifact. A deployable artifact would need to pin the 27 feature calculations,
the after-close clock, the per-cell scaler, coefficients, source digests, and a
freshness contract in the normal DSKIT release path.

The next model-development step should add a reviewed, point-in-time option
surface feature set and test whether it improves these same 21 conditional-scale
forecasts. The next evidence step for this ElasticNet is a genuinely future
fold; neither step is permission to trade.

## Evidence

Local ignored evidence is under `pipeline_runs/elasticnet_hpo_20260928/`:

- global protocol SHA-256:
  `86555dc1ec9a2ec0324ab3ac8bdc3745461c464c288ef0715635be184cc15632`
- 90-candidate search SHA-256:
  `e9d80c7c49a4370773c54a714d885d2081ee2d36e7e289179c9359ab4bebf94e`
- locked 147 folds SHA-256:
  `bacd53011992467c9d27fb0f93bd77297ca55a041f7af0ad44bd2309f691f5f4`
- locked summary SHA-256:
  `259ed47b52097285ccb1221b7b046ced56878d6c3a435d031794d251b3b5b2cd`
- cell grid SHA-256:
  `c94215d3fadd06c2879e5b10f0962dafa9bec3361534afa31524cfaedbbaf222`
- final coefficient descriptions SHA-256:
  `67fcda6a078bcdd35fa14a94d8aa08446d0bf4208061b3b4ea2f8647526a8b7a`

This memo supersedes only the final-model choice in
`2026-09-28-fixed-feature-model-zoo-selection.md`; the earlier zoo measurements
remain valid.
