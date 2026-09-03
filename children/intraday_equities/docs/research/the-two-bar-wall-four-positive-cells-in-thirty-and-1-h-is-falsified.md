## Question

Across 30 walks (5 model classes x H=1/2/3/20/30/60, 20 folds each), where
does a forecast beat the training-mean benchmark — and does the H=1 gain
decay the way bid-ask bounce predicts?

## Finding

**Four positive cells out of thirty. All at H <= 2, all low-capacity.**

Clark-West gain (%, model MSPE against the training mean's):

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | **+0.0898** | **+0.2337** | -7.40 | -10.61 | -4.90 |
| 2 | **+0.0096** | **+0.0796** | -7.72 | -11.17 | -5.04 |
| 3 | -0.0206 | -0.0049 | -8.42 | -11.72 | -5.25 |
| 20 | -0.0285 | -0.6512 | -11.86 | -12.88 | -17.01 |
| 30 | -0.0948 | -1.1620 | -39.57 | -37.43 | -28.04 |
| 60 | -0.1144 | -1.0530 | -53.71 | -47.93 | -38.83 |

Both positive curves cross zero between H=2 and H=3: **a two-bar wall.**
The three nets never enter positive territory at any horizon, and their
gain worsens monotonically with capacity and with h — at H=60 the GRU
forecasts 54% worse than a constant.

**The pre-registered 1/h bounce prediction is FALSIFIED, and the test did
not cleanly replace it.** From +0.090% at H=1, bounce predicted +0.045%
at H=2 (a fixed covariance term against label variance growing like h).

- **Ridge fell 9.4x** to +0.0096% — far too fast for bounce.
- **LightGBM fell 2.9x** to +0.0796% — close to the predicted 2x.

The two models disagree about the DECAY of the same effect. Ridge's curve
rules bounce out; LightGBM's is consistent with it. Whatever survives
into minute two is visible to the tree and nearly invisible to the
linear model, so it is not a pure linear autocovariance. **Mechanism
unnamed.**

**The finding worth chasing is the divergence between rank and accuracy.**
LightGBM's val IC decays ~30x more slowly than its gain:

| H | lgbm val IC | folds positive | lgbm gain |
|---|---|---|---|
| 1 | +0.0538 | 20/20 | +0.234% |
| 3 | +0.0302 | 19/20 | -0.005% |
| 20 | +0.0136 | 17/20 | -0.651% |
| 60 | +0.0200 | 13/20 | -1.053% |

Rank information persists far past the horizon where forecast accuracy
dies. For a PREDICTION-ONLY model feeding a selecting optimizer (the
owner's framing: cost arithmetic belongs downstream), that gap is the
whole question — and nothing in the current scan measures it. Two
additions would: **calibration** (regress y on yhat per fold; slope ~1
means the magnitude can be sized on, slope << 1 means only the ordering
is usable) and **per-timestamp cross-sectional IC** (an optimizer
chooses AMONG names at one instant; every number here is pooled per name
over time).

**Rejection counts remain untrustworthy on their own.** h01-gru rejects
12/60 while forecasting 7.4% worse than the mean; h30-lstm matches
ridge's 7/60 from a forecast 37% worse. Clark-West adds back the variance
a nested model pays to estimate its parameters, so it rejects on the
POPULATION claim. Only the gain column separates a forecast from that
correction.

## Sources

- 30 walks, `configs/run-multi3-h{01,02,03,20,30,60}-{ridge,lgbm,gru,lstm,tft}.json`.
  Folds: first 2022-05-06, step 63d, val 63d, embargo 5d, train 730d,
  last val inside 2025-11-30. n_train ~110k, n_val ~9.5k.
- ADR-0059 (label), 0060 (estimator knob), 0061 (ZooEstimator),
  0062 (lead grid as a document knob) — all PROPOSED, none ratified.
- Prior: `h-1-is-the-only-positive-gain-cell-the-shared-px-t-channel-explains-it.md`
  (its 1/h prediction is the one falsified here).
- Untested and cheap: a VWAP `price_field` variant of h01-ridge/h01-lgbm.
  `vwap` averages every print in the minute, so its bounce term is ~b^2/n;
  a gain that collapses under it was bounce after all.
- NOT corrected for multiplicity: 60 name-folds x 5 models x 6 horizons.
