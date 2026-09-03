## Question

Across five model classes and four horizons (18 of 20 walks complete),
where does the forecast actually beat the training-mean benchmark — and
does ADR-0059's vol-normalised, SPY-residual label change the answer?

## Finding

**Two cells out of eighteen have a POSITIVE Clark-West MSPE gain, and
both are H=1.** Everything else forecasts worse than the mean.

| walk | val IC | pos | train IC | val MSPE | gain % | p<.05 |
|---|---|---|---|---|---|---|
| h01-ridge | +0.0340 | 19/20 | +0.0365 | 0.797 | **+0.090** | 24/60 |
| h01-lgbm | +0.0538 | 20/20 | +0.1439 | 0.795 | **+0.234** | 29/60 |
| h01-tft | +0.0262 | 20/20 | +0.1609 | 0.836 | -4.90 | 9/60 |
| h01-lstm | +0.0198 | 20/20 | +0.2532 | 0.882 | -10.61 | 9/60 |
| h01-gru | +0.0157 | 17/20 | +0.2515 | 0.856 | -7.40 | 12/60 |
| h20-ridge | +0.0064 | 14/20 | +0.0162 | 1.345 | -0.028 | 4/60 |
| h30-ridge | +0.0092 | 11/20 | +0.0346 | 1.575 | -0.095 | 7/60 |
| h60-ridge | +0.0184 | 14/20 | +0.0495 | 1.637 | -0.114 | 8/60 |
| h20/30/60 lgbm | +0.009..+0.020 | | +0.11..+0.29 | 1.35..1.65 | -0.65..-1.16 | 1-6/60 |
| h20/30/60 nets | -0.006..+0.015 | | +0.07..+0.54 | 1.5..2.5 | -12..-54 | 1-7/60 |

Two orderings run through the whole grid: **gain falls with horizon**, and
**gain falls with capacity**. Ridge is the best forecaster at every H;
the nets are the worst everywhere, reaching -54% at H=60 with train IC
+0.52 against a negative val IC.

**Rejection counts do not track skill.** h01-gru rejects 12/60 while
forecasting 7.4% worse than the mean; h30-lstm matches ridge's 7/60 from
a forecast 37% worse. Clark-West adds back the variance a nested model
pays for estimating parameters, so it rejects on the POPULATION claim.
Only the gain column separates a forecast from an artifact of that
correction.

**The H=1 result is real and the owner's reading of it is correct as
stated:** both low-capacity models beat the mean benchmark out of sample,
19/20 and 20/20 folds, with train and val MSPE nearly equal (0.77/0.80) —
not overfitting, and no leak found (features use bars <= t; sigma and beta
are strictly causal). It is a martingale rejection at one minute.

**The open question is what is being predicted, not whether.** The label
is `[log(px[t+h]/px[t]) - beta_t*log(qx[t+h]/qx[t])] / (sigma_t*sqrt(h))`.
Both ADR-0059 transforms leave `px[t]` untouched: the residual subtracts
SPY, whose print noise is independent (and by removing market variance it
RAISES bounce's share of what remains), and sigma_t is a scalar measurable
at t that rescales numerator and noise alike. Meanwhile
`ret_lag_0 = log(px[t]/px[t-1])` carries the same `px[t]` with the
opposite sign. If a print lands at the ask, `ret_lag_0` reads high and the
next return reads low — negative feature/label correlation that exists
even under a perfect random walk.

**Why this matters even though the model is prediction-only** (an
optimizer selects downstream, so cost arithmetic is out of scope here): a
bounce signal correctly forecasts the next PRINT, not the next value. The
predicted reversion is the price returning to mid, and acting on it means
crossing to the ask that generated the signal. That is a validity
question about the target, not a fee.

**Pre-registered discriminator, declared before the runs.** With bounce
variance `b^2` and per-minute return variance `sigma^2`, label variance
grows as `sigma^2*h + 2b^2` while covariance with `ret_lag_0` stays at
`-b^2`: correlation falls as 1/sqrt(h) and the MSPE gain as 1/h. So
**+0.090% at H=1 predicts ~+0.045% at H=2 and ~+0.030% at H=3.** A gain
decaying SLOWER than that is not bounce, and the finding is genuine
short-horizon reversion. Ten walks (5 models x H=2,3) are queued.

**What would settle it outright:** a midquote tape, or a label that skips
a bar (features ending at t-1, label t -> t+h) so no price appears on both
sides. Neither is built.

## Sources

- 18 completed walks, `configs/run-multi3-h{01,20,30,60}-*.json`; folds
  first 2022-05-06, step 63d, val 63d, embargo 5d, train 730d, last val
  inside 2025-11-30; n_train ~110k, n_val ~9.5k.
- ADR-0059 (the label), ADR-0060 (estimator knob), ADR-0061
  (`ZooEstimator`), ADR-0062 (lead grid as a document knob).
- `dskit/pipeline/stats.py` `no_information_test` — benchmark is the
  TRAIN mean, one-sided, Newey-West with `lead//period_minutes - 1` lags.
- Prior: `ridge-and-lightgbm-both-sit-at-the-null-on-jpm-lly-xom.md`.
- NOT corrected for multiplicity: 60 name-folds x 5 models x 4 horizons.
