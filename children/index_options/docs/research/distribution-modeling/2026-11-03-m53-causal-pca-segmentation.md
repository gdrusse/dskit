## Question

Can unsupervised low-rank structure (PCA) or simple, causally defined regimes produce a genuine learned correction to the final M53 CDF, rather than a backtest lift from its existing calibrated-market/blend machinery?

## Inputs and join contract

- Final M53 forecast: scores/edge-method-53-qa-mlp64-rolling-by-dispersion/forecasts.parquet (SHA256 ff4cfcfcddf550d8efb74e7dbfead0ab5fb343b028fed254077212ee06723120).
- Feature panel: panel/panel-cache/frame.parquet (SHA256 186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144).
- Raw forecast keys repeat because each rolling fold writes historical fit rows as well as held-out rows. The scored baseline is exactly band == "val" and fitted == True, yielding 527,079 unique (symbol, quote_date, expiry) rows. That slice joins the panel one-to-one with no lost forecast row.
- Target: terminal_return / reference_scale; every feature is as-of quote_date. The correction is a location shift of the final M53 CDF, F(z - clip(delta, -1, 1)), where the training target is the realized standardized return minus the interpolated M53 median.
- Metric: locked 13-point threshold-weighted CRPS grid (z=-3,-2.5,...,3, endpoint trapezoid half-weights), reproducing M53 F3 = 0.500383638.

The screen uses F1–F2 as causal history (178,531 rows), divided chronologically into an inner fit block (142,824) and monitor block (35,707), then evaluates once on F3 / 2021 (119,812 rows). 2020 only enters as already-settled causal history; it is not a reported candidate score. Fold 4/5 are not evaluated because no candidate passed the F3 gate.

All heads are ridge location heads with no intercept and penalty grid {1e3, 1e4, 1e5, 1e6, 1e7, 1e8}. A zero head is an admissible null and exactly recovers M53.

## PCA screen

PCA was fit only on the inner historical feature matrix: per-feature inner mean imputation and standardization, followed by eigendecomposition of X'X. The top 1, 2, 3, 5, and 8 components were tried; the full 46-feature ridge is the non-PCA control. Penalty and PCA size used the historical monitor; F3 is the primary 2021 check.

| head | historical monitor gain | F3 / 2021 gain | reading |
| --- | ---: | ---: | --- |
| 1 PC | +0.7597% | -0.4705% | strong monitor fit, decisively fails 2021 |
| 2 PCs | +0.2986% | -0.2561% | fails |
| 3 PCs | +0.0076% | -0.0427% | fails |
| 5 PCs | +0.0003% | -0.0378% | fails |
| 8 PCs | +0.0101% | -0.0302% | fails |
| full 46-feature ridge | +0.0036% | -0.0322% | fails |

The one-PC result is the clearest evidence against proceeding: it improves the historical monitor from 0.494457839 to 0.490701222 but worsens independent F3 from 0.500383638 to 0.502737758. Reducing the dimension does not turn this into transferable signal.

## Regime / segmentation screen

Twelve predeclared three-way regimes were tested: rv_22, own_iv, skew, curvature, relative spread, rn_median, rn_iqr, prior term slope, implied-minus-realized variance, calendar DTE, life fraction, and 22-day momentum. For each, the two cutpoints are the causal history tertiles. The head contains only the three segment indicators, so it learns a separate small M53 location correction per regime; missing segment values fall back to unshifted M53.

Every regime selected the largest tested penalty (1e8) and was slightly worse than M53 both on the historical monitor and F3. The best monitor case was prior term slope at -0.0119% monitor / -0.0027% F3; the best F3 case was relative-spread segmentation at -0.0028% F3. These are tiny finite-penalty losses, not positive signal: the admissible null (zero coefficients) wins all twelve.

## Decision

Stop this branch of feature engineering. Neither unsupervised PCA nor coarse conditional location shifts provide evidence of learning beyond M53. No F4/F5 confirmation was justified, no model is promoted, and M53 remains unchanged.

The useful constraint for the next idea is sharper: any proposed lift must change the prediction problem or correction operator and demonstrate a positive 2021 effect before later-fold testing. More feature compression or simple regimes would be noise mining under this evidence.

