# Predictor uncertainty output for a robust optimizer

## Question

What uncertainty should a weak-signal, walk-forward US-equity intradaily return predictor output, particularly when each name may use a different horizon and a downstream robust optimizer—not a human—will consume it?

## Finding

**Recommended output: a calibrated quantile-and-scenario product, not one standard deviation or one interval.**

For every `(timestamp, name, horizon)`, emit:

1. A **non-crossing conditional quantile grid**, for example \(q_{.01},q_{.05},q_{.10},q_{.25},q_{.50},q_{.75},q_{.90},q_{.95},q_{.99}\), trained with pinball loss.
2. **Rolling conformal corrections** for selected central and one-sided intervals, using asymmetric, volatility-normalized conformity scores.
3. A synchronized bank of **joint standardized residual vectors** across names, with timestamps or block identifiers, plus the current per-name scale forecasts.
4. Calibration metadata: calibration-window dates, effective sample size, realized coverage and interval score by name, horizon, time-of-day, volatility regime, and selected-versus-unselected trades.

This separates three things an optimizer may need:

- **Quantiles** support chance constraints, VaR/CVaR, asymmetric downside penalties, and inverse-CDF scenario generation.
- **Joint residual scenarios** preserve cross-name dependence and support scenario-based robust or distributionally robust optimization.
- **Covariance** must be estimated from joint scenarios or a separate conditional covariance model. Marginal quantiles or per-name prediction intervals do **not** identify covariance.

Also distinguish a predictive interval for the *next realized return* from a confidence set for the *conditional expected return*. Treating a wide return prediction interval as an uncertainty set for expected alpha is conceptually wrong and usually excessively conservative.

### Which methods are actually calibrated?

**Quantile heads / pinball loss:** Best base representation. Quantile regression is naturally robust to non-Gaussian tails and allows skewness and heteroskedasticity without assuming moments. Finance evidence supports conditional quantiles for intraday VaR: Cenesizoglu and Timmermann model 30-minute returns and find important time-of-day, asymmetry, and kurtosis effects beyond volatility. But pinball training alone provides no finite-sample out-of-sample coverage guarantee. Sparse tail observations create unstable 1%/99% heads, separately fitted heads can cross, and hyperparameter selection on the same validation period can make apparent calibration optimistic.

Evaluate each quantile by pinball loss and empirical exceedance frequency. Evaluate interval pairs with the Gneiting–Raftery interval score, which rewards sharp intervals but penalizes misses. Coverage alone can be gamed by making intervals arbitrarily wide.

**CQR:** Romano, Patterson, and Candès combine lower and upper quantile models with a conformal residual correction. Under exchangeability, CQR gives finite-sample *marginal* coverage while adapting interval width to heteroskedasticity. This is a strong base method, but ordinary CQR’s theorem does not survive serial dependence, overlapping return horizons, market-wide shocks, or regime drift unchanged. For intraday returns, use only past data, purge overlaps, recalibrate sequentially, and normalize conformity scores by a strictly ex-ante volatility estimate. Calibrate separately by horizon; either calibrate per name or pool standardized scores across names while auditing name-conditional coverage.

**EnbPI:** Xu and Xie’s bootstrap-ensemble method avoids exchangeability and obtains approximately valid sequential coverage under assumptions including strongly mixing errors and adequate regression estimation. It is useful when residual dynamics are persistent and calibration data are scarce because it avoids a fixed train/calibration split. It is not distribution-free against arbitrary breaks: strong mixing, stable error behavior, and ensemble quality matter. Its usual residual interval can also be inefficient under skewness or changing scale. Abrupt openings, halts, news shocks, and volatility transitions are precisely where its historical residual pool may lag.

**Weighted and adaptive time-series conformal:** Barber, Candès, Ramdas, and Tibshirani show how weighted conformal quantiles degrade more gracefully under nonexchangeability; they do not make arbitrary drift harmless. Gibbs and Candès’ adaptive procedures target long-run coverage under changing distributions and adapt the learning rate to unknown shifts. These are preferable to frozen split conformal for deployment, but long-run marginal coverage can conceal severe short-lived undercoverage immediately after a regime change. Aggressive adaptation can oscillate and widen intervals after errors rather than before them.

There is no nontrivial, assumption-free guarantee of exact conditional coverage for each name, volatility regime, or selected trade. Barber et al.’s impossibility result is directly relevant. A strategy that trades only extreme forecasts also changes the target: unconditional coverage over all rows does not establish coverage among selected trades.

**MC dropout / deep ensembles:** These estimate model or parameter dispersion, not frequentist predictive coverage. MC dropout depends materially on dropout rate and variational approximation. Deep ensembles generally provide stronger empirical uncertainty than MC dropout, but ensemble agreement can remain high-confidence and wrong under a shared regime shift. Neither is calibrated on fat-tailed intraday returns merely because multiple forward passes were made. They can be useful as features, ensemble diversity diagnostics, or CQR base learners—but should still receive out-of-sample conformal calibration.

**NGBoost / heteroskedastic Gaussian:** NGBoost cleanly outputs a full conditional distribution and can learn input-dependent scale. The default Gaussian family is a poor final assumption for skewed, heavy-tailed, discontinuous intraday returns: one variance parameter cannot represent asymmetric tails, and likelihood training may underestimate crisis risk. A Student-\(t\), mixture, or flexible distribution is more defensible, but remains model-dependent and requires PIT, tail exceedance, and proper-score validation. Conformalizing its quantiles improves marginal coverage but does not repair an inadequate joint dependence model.

**Empirical residual distributions:** Raw pooled residuals fail because return scale, time-of-day, name, and regime are not stationary. A **filtered** empirical distribution is much more useful: divide residuals by ex-ante conditional scale, retain synchronized cross-name vectors, resample rows or short blocks, then restore current scales. This supplies the optimizer with realistic non-Gaussian joint scenarios. It still fails when the volatility filter is wrong, dependence changes, extreme events are absent from the window, or overlapping horizons are resampled as independent.

### Decision

Use **non-crossing quantile heads plus rolling, volatility-scaled CQR/adaptive conformal calibration** as the declared marginal uncertainty output. In parallel, publish **joint filtered residual scenarios** for portfolio dependence. Do not declare MC dropout variance, ensemble spread, Gaussian NGBoost scale, or a raw residual standard deviation “calibrated uncertainty.”

Walk-forward acceptance should require quantile exceedance rates and pinball loss; central and one-sided coverage, interval score, and average width; violation clustering; results by name, horizon, time-of-day, volatility bucket, and post-break window; coverage among names actually selected by the strategy; joint-scenario covariance and portfolio-tail backtests.

Finance-specific evidence shows why this matters: Clements and Taylor find that standard interval tests can be inappropriate for intraday data with periodic heteroskedasticity, while Rice, Wirjanto, and Zhao obtain useful intraday VaR forecasts only after explicitly modeling the intraday return curve. Calibration here is therefore a repeatedly measured walk-forward property—not a permanent attribute of a model class.

## Sources

- Romano, Patterson & Candès, “Conformalized Quantile Regression,” NeurIPS 2019: https://proceedings.neurips.cc/paper/2019/hash/5103c3584b063c431bd1268e9b5e76fb-Abstract.html
- Xu & Xie, “Conformal Prediction for Time Series,” *IEEE TPAMI* 45(10), 2023. https://doi.org/10.1109/TPAMI.2023.3272339
- Barber, Candès, Ramdas & Tibshirani, “Conformal Prediction Beyond Exchangeability,” *Annals of Statistics* 51(2), 2023. https://doi.org/10.1214/23-AOS2276
- Gibbs & Candès, “Conformal Inference for Online Prediction with Arbitrary Distribution Shifts,” *JMLR* 25, 2024: https://www.jmlr.org/papers/v25/22-1218.html
- Barber et al., “The Limits of Distribution-Free Conditional Predictive Inference,” *Information and Inference* 10(2), 2021. https://doi.org/10.1093/imaiai/iaaa017
- Gneiting & Raftery, “Strictly Proper Scoring Rules, Prediction, and Estimation,” *JASA* 102(477), 2007. https://doi.org/10.1198/016214506000001437
- Gneiting, Balabdaoui & Raftery, “Probabilistic Forecasts, Calibration and Sharpness,” *JRSS B* 69(2), 2007. https://doi.org/10.1111/j.1467-9868.2007.00587.x
- Clements & Taylor, “Evaluating Interval Forecasts of High-Frequency Financial Data,” *Journal of Applied Econometrics* 18, 2003. https://doi.org/10.1002/jae.703
- Cenesizoglu & Timmermann, “A Simple Two-Component Model for the Distribution of Intraday Returns,” *European Journal of Finance* 18(9), 2012. https://doi.org/10.1080/1351847X.2011.601649
- Rice, Wirjanto & Zhao, “Forecasting Value at Risk with Intra-day Return Curves,” *International Journal of Forecasting* 36(3), 2020. https://doi.org/10.1016/j.ijforecast.2019.10.006
- Christoffersen, “Evaluating Interval Forecasts,” *International Economic Review* 39(4), 1998. https://doi.org/10.2307/2527341
- Duan et al., “NGBoost: Natural Gradient Boosting for Probabilistic Prediction,” ICML 2020: https://proceedings.mlr.press/v119/duan20a.html
- Gal & Ghahramani, “Dropout as a Bayesian Approximation,” ICML 2016: https://proceedings.mlr.press/v48/gal16.html
- Lakshminarayanan, Pritzel & Blundell, “Simple and Scalable Predictive Uncertainty Estimation Using Deep Ensembles,” NeurIPS 2017: https://proceedings.neurips.cc/paper/2017/hash/9ef2ed4b7fd2c810847ffa5fa85bce38-Abstract.html
