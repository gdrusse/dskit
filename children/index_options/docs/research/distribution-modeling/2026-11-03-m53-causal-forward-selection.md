## Question

Can a deliberately small, causal feature head learn a stable correction to the frozen M53 option-CDF forecast, rather than merely benefit from M53's rolling calibration and blend?

## Finding

No. The bounded forward-selection screen found no feature that cleared the predeclared 0.05% 2021 improvement floor and survived the historical monitor. The best first feature, `rn_median`, improved 2021 threshold-weighted CRPS from 0.500383638 to 0.500315668 (0.0136%), below the floor. Its frozen 2022--2023 confirmation was worse than M53: 0.523054767 versus 0.522610109 (0.0851% worse).

The diagnostic second step supplied the stop signal. Adding `chain_log_rel_spread` after `rn_median` improved the fit score by 0.0807% and the 2021 selector by 0.0996%, but made the chronological monitor 0.2551% worse. This is the intended train--validation separation, so the screen stopped without evaluating later prefixes. It is evidence against a learned residual head in this pool, not evidence that every possible feature or target is unlearnable.

## Sources

This is an exploratory, non-promotional screen. It leaves decision-path rows A0002--A0005 and A0008 unchanged: no recipe was promoted and no sealed holdout was opened. It uses M53's saved final forecast CDF as the baseline, from `scores/edge-method-53-qa-mlp64-rolling-by-dispersion/forecasts.parquet` (SHA-256 `ff4cfcfcddf550d8efb74e7dbfead0ab5fb343b028fed254077212ee06723120`) and its fit description (SHA-256 `f9ddf3ff2f45a27f0fd9c37065828ec4a8e471e65b0d2317108cfb433167e04d`). Features and realised outcomes came from `panel/panel-cache/frame.parquet` (SHA-256 `186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144`).

Forecasts joined the panel exactly on `(symbol, quote_date, expiry)`. The panel key was unique; 527,079 eligible forecast rows joined with no cardinality change or missing outcome. Recomputing the declared 13-threshold trapezoidal weighted CRPS reproduced the saved M53 2020--2023 score exactly: 0.525580619.

The residual head was a no-intercept Ridge location correction. For baseline CDF `F` and standardized feature vector `x`, it predicts `delta = x beta` and scores `F(z - delta)`; `delta` is clipped to [-1, 1] standardized-return units. Thus `beta = 0` is exactly M53, rather than a different intercept-only forecast. The training target was realised standardized return less M53's interpolated median. For each outer year, features were standardized only on historical rows, missing values were set to that history mean, and the Ridge penalty was selected from 10^3 through 10^8 on the last chronological 20% of historical quote dates.

The 46-feature pool was deliberately limited to quote-time market/Theta inputs: realised-volatility and momentum, implied-volatility level and changes, skew and curvature, term slopes, liquidity/open-interest/flow measures, risk-neutral shape summaries, and lifecycle fields. It excluded terminal, settlement-labelled, and ticker-identity fields.

For order selection only, 2021 was the outer selector. COVID-year 2020 observations could enter only as already-settled causal history for the 2021 fit; they never ranked features or determined the stopping rule. The 2021 fit used 172,328 rows whose `settlement_date` preceded the first 2021 quote, with 130,337 inner-training and 33,612 chronological-monitor rows. 2022 and 2023 were untouched confirmation years.

| Stage | Feature prefix | Fit gain | Historical monitor gain | 2021 gain | Result |
|---|---|---:|---:|---:|---|
| Start | none | -- | -- | -- | M53 2021 = 0.500383638 |
| 1 | `rn_median` | +0.0470% | +0.2535% | +0.0136% | below 0.05% floor; not selected |
| 2 diagnostic | `rn_median`, `chain_log_rel_spread` | +0.0807% | -0.2551% | +0.0996% | stopped: monitor diverged |

The frozen first-feature confirmation used a new causal fit for each year. In 2022 the selected penalty was 10^8 and the correction was effectively zero, but still slightly worse (0.518034865 versus 0.518033460). In 2023 the selected penalty was 10^3 and it was materially worse (0.528382936 versus 0.527467805). The row-weighted 2022--2023 result was 0.523054767 versus M53 0.522610109.

No source model, saved forecast, input panel, decision-path row, or sealed holdout was changed or opened.
