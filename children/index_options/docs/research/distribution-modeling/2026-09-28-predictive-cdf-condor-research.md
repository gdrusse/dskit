## Question

Which approaches best fit our need for a curve that accepts a price and returns
its probability at an actual SPY, QQQ, or IWM expiry, enabling expected
iron-condor payoff calculations?

Scope: literature and repository review, 2026-09-28. No CDF model was trained
or selected empirically during this research. The sources establish useful
methods, but do not establish a winner on our exact data and horizons.

## Finding

My recommended first comparison is a volatility-scaled empirical distribution,
a directly estimated monotone CDF, LightGBM quantiles, and a small mixture-density
MLP. The direct CDF is my preferred first challenger because it estimates the
queried probabilities explicitly. The mixture MLP is a particularly good fit
for a smooth curve with fast CDF and expected-payoff calculations. Those are
design judgments, not claims that either has already beaten the baseline.

### What the curve represents

For entry information x, entry price S0, and actual expiry e, predict the
distribution of terminal log return R = log(S_e / S0). Then

`F_price(K | x,e) = F_return(log(K/S0) | x,e)`.

Each index/date/expiry gets one curve for all possible prices. The 123 puts and
123 calls in our SPY example query the same distribution; they do not supply
246 independent return outcomes. Share model parameters across maturities,
with exact calendar DTE and remaining trading sessions as inputs. There is
no requirement to fit 135 unrelated models because the report has 135 cells.

The CDF itself is an increasing curve from zero to one. A density curve is
optional. The intended interface is `cdf(price)`, `quantile(probability)`,
and `expected_spread_or_condor_payoff(strikes)`. For a < b, F(b)-F(a) gives
P(a < S_e <= b). Endpoint conventions matter for empirical distributions.

Use settlement-consistent raw prices, splits, and contract adjustments;
dividend-adjusted total returns do not directly equal option settlement
returns. The existing daily-RMS forecast can be a feature or scale estimate.
Its point-forecast skill does not establish the accuracy of this new CDF.

### Approaches to compare

**1. Volatility-scaled empirical CDF: the reference to beat.** Standardize
historical terminal returns using forecasts made without seeing those
outcomes, learn their empirical shape, then scale it for today's conditions
and exact maturity. Compare with a horizon-conditioned empirical CDF without
the new volatility model to measure its incremental value. This preserves
observed asymmetry, has a valid staircase CDF, and computes payoff expectations
by averaging samples. It cannot establish behavior beyond historical support,
and a pooled shape cannot automatically capture changing tail asymmetry.
The idea is related to filtered historical simulation, although a direct
terminal-label model differs from simulating daily volatility paths.
[FHS research](https://onlinelibrary.wiley.com/doi/pdf/10.1111/1468-036X.00175).

**2. Monotone distribution regression / transformation model: preferred direct
CDF challenger.** Estimate P(R <= c | x,e) over cutoffs c, tying predictions
together so probabilities increase with the cutoff. Ordered-probability
regression has direct return-forecasting precedent. A continuous formulation
is F_R(r|x,e) = G(h(r,x,e)), with a known CDF G and an increasing transformation
h with appropriate endpoint behavior. Start with a regularized, low-dimensional
spline; allow features to affect spread and shape. Independent logits can
cross, while a simple proportional-odds model may be too restrictive. Test
sensitivity to knots and tail assumptions.
[Return-distribution regression](https://arxiv.org/abs/1711.05681),
[conditional transformation models](https://arxiv.org/abs/1201.5786),
[neural extensions](https://arxiv.org/abs/2010.07860).

**3. LightGBM quantiles: practical nonlinear challenger.** Fit a predeclared
quantile grid, correct crossing, and invert the ordered curve into a CDF.
LightGBM supplies quantile loss and its alpha parameter. Begin with a modest
grid and check whether refinement changes expected spread losses. Quantile
forests are an alternative producing a weighted empirical distribution in
one fit. Neither a few quantiles nor interpolation identifies unobserved
tails; specify and test endpoint treatment.
[LightGBM parameters](https://lightgbm.readthedocs.io/en/stable/Parameters.html),
[noncrossing curves](https://arxiv.org/abs/0704.3649),
[quantile forests](https://www.jmlr.org/papers/v7/meinshausen06a.html).

**4. Mixture-density MLP: preferred small neural challenger.** Have an MLP
predict weights pi_j, means mu_j, and positive scales sigma_j of a small
Gaussian mixture for terminal log return. Compare one component as a control
with two or three components. Then

`F_price(K) = sum_j pi_j * Phi((log(K/S0)-mu_j)/sigma_j)`.

It supplies a smooth CDF at arbitrary prices and permits asymmetry. Because
price is a mixture of lognormals, expected calls and puts have analytic formulas.
These are physical expected payoffs; no risk-neutral drift is imposed. Constrain
scales away from zero, regularize, use multiple seeds, and validate on later
dates: mixture likelihood can otherwise collapse around individual outcomes.
Its extrapolated tails remain assumptions.
[Bishop's mixture-density networks](https://www.microsoft.com/en-us/research/publication/mixture-density-networks/),
[mixture likelihood singularities](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/bishop-aistats01.pdf).

**Additional comparisons if needed.** NGBoost/distributional boosting predicts
parameters of a selected distribution with trees. A one-dimensional monotone
spline flow provides a CDF through its inverse transformation and base CDF.
Both are useful candidates, but generic density-estimation benchmarks do not
prove an advantage for these index observations. GARCH/FHS paths become more
relevant if early exits or other path-dependent outcomes enter the objective.
[NGBoost](https://proceedings.mlr.press/v119/duan20a.html),
[neural spline flows](https://arxiv.org/abs/1906.04032),
[arch forecasting](https://arch.readthedocs.io/en/latest/univariate/forecasting.html).

Student-t is a useful tail-sensitive control with a mathematical caveat:
a literal Student-t log-return has no finite positive exponential moment.
Its power-law tail makes E[exp(R)] and unbounded call-payoff expectations
diverge. Bounded condor payoffs remain integrable; compute the capped payoff
directly rather than subtracting infinite leg expectations. Finite Gaussian
mixtures of log returns avoid this issue for the initial MLP candidate.
[Student-t density](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html).

### CDF evaluation and expected return

For K1 < K2 < K3 < K4 (long put, short put, short call, long call), the
terminal-payoff identity per share is

`E[P&L] = credit - integral[K1,K2] F(s) ds`
`                - integral[K3,K4] (1-F(s)) ds - costs`.

This follows by integrating each capped spread loss. Four endpoint
probabilities alone do not identify expected partial losses. A full CDF,
numerical integration, or averaging bounded payoffs under samples does.
The expression assumes intact expiry payoff and excludes financing. Specify
cash-flow timing and a capital denominator before reporting percentage returns.

For the archived SPY example on 2025-11-12, expiring 2025-12-12, the 635/657
puts and 708/720 calls collected $3.63/share at quoted bid/ask. Expected dollars
for one standard condor are $363 minus 100 times the two integrals and costs.
No probabilities or expected profit for that example have been fitted here.

A derived sensitivity bound: if CDF error is at most epsilon throughout both
spreads, expected-P&L error is at most
`100 * epsilon * [(K2-K1)+(K4-K3)]`.
For these $22 and $12 wings, a one-percentage-point uniform CDF error allows
up to $34 expected-P&L error. This is a mathematical bound, not measured model
error or a confidence interval.

For grid-based curves, monotone piecewise-linear interpolation is a transparent
starting point. PCHIP provides a smooth monotone curve inside ordered knots.
Disable uncontrolled extrapolation and specify tails. Smoothing adds assumptions,
not observations. Check expected payoff before/after interpolation and at
finer grid resolutions.
[SciPy PCHIP](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html).

Numerical verification: CDF integration and direct payoff integration agreed
within 1e-9 dollars/share for a synthetic single-lognormal case and a synthetic
two-component lognormal mixture. These checks validate the payoff identity,
not any forecast or trading result.

### How to establish which approach is best here

Use common rows and feature bundles across methods. First compare current
core features plus causal scale estimates, then reassess shape features such
as skew, term structure, and downside returns from the existing wider space.
Features rejected for volatility MSE can help a return CDF. Feature selection
and HPO must use distribution losses within development folds.

Fit, calibrate, and evaluate chronologically. Purge labels extending into the
next stage; every residual used for shape or calibration must have settled
before use. The already inspected 2019-2025 data remain research validation,
not an untouched test. Horizon-conditioned residuals should test, rather than
assume, Gaussian innovations or square-root-time scaling.

Measure full CRPS, predeclared threshold-weighted CRPS in relevant spread
regions, Brier scores at strikes, and calibration by index, horizon, and regime.
Weighting regions must be known before outcomes and common across models.
Keep scoring units comparable and separately report expected-payoff errors in
dollars. Select models using proper distribution scores, then assess a frozen
condor-ranking rule on later data as the economic check.
[Proper weighted scores](https://www.jstatsoft.org/article/view/v110i08).

A separate historical calibration block can learn a monotone recalibration
map of predicted probabilities. Aggregate calibration does not establish
calibration for every index/horizon/tail. Conformal intervals can supplement
the analysis, but one interval does not define a CDF; exchangeability
guarantees do not automatically apply to overlapping financial observations.
[Regression recalibration](https://proceedings.mlr.press/v80/kuleshov18a.html),
[adaptive inference under shift](https://arxiv.org/abs/2208.08401).

Report fit/calibration/validation rows, unique quote dates, unique expiries,
and exceedance counts. Use date blocks spanning at least the longest
overlapping label for uncertainty assessment, test longer blocks, and inspect
nonoverlapping samples. Keep same-date indexes together for combined results.
Block counts do not prove independence. Distinguish predictive intervals for
future outcomes from uncertainty bands on an estimated CDF or expected P&L.
Our 68,084 validation rows are dependent date/expiry observations.

The target probabilities are physical probabilities of realized outcomes.
Option-implied risk-neutral CDFs incorporate risk premia and may serve as
features or benchmarks. ETF early assignment, dividends, and execution need
additional treatment beyond the terminal curve; the existing conservative
charge is an approximation.
[New York Fed](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr677.pdf),
[OIC condor mechanics](https://www.optionseducation.org/strategies/all-strategies/short-condor).

### Existing capabilities and next deliverable

Inspection at 7d690d68 and a sweep of nine refs/three other worktrees found:

- `distribution_scores.py` already provides a sample CDF, quantiles, CRPS,
  threshold-weighted CRPS, threshold Brier, and PIT/Berkowitz diagnostics.
  Interpret statistical significance with dependence in mind.
- `distribution_models.py` provides empirical and conditional-scale shapes.
  `ScaleModelLocationScale` explicitly documents learning shape from in-sample
  forecasts and identifies cross-fitting as missing.
- The generic sklearn pack fits configured estimators including LightGBM.
  Its scalar prediction interface alone does not compose a calibrated CDF.
  The dedicated LightGBM pack estimates log-volatility scale.
- `outcome_interval.py` supplies dependent-residual interval/scenario primitives,
  rather than a trained continuous CDF for this study.
- The child's `CondorGeometry.evaluate` and `condor_payoff` already average
  four-leg payoffs under samples. The default empirical model uses 200 draws,
  so its CDF values are in multiples of 0.5 percentage points. Check numerical
  resolution against exact empirical averages or analytic expectations.

This expands the 2026-09-23 model ladder and the initial A0602 proposal without
adopting the older notes' unverified improvement estimates. Recommended next
deliverable: a bounded experiment specification for the four approaches,
with common exact-expiry labels, chronological calibration, and payoff-resolution
checks. Optimization follows evidence on distribution quality and expected
payoff rankings. No training, implementation, or winning architecture is
claimed from this literature review.

## Sources

Primary papers, author/institution publications, and first-party documentation
are linked beside their claims. Local evidence is the inspected code and
corrected exact-expiry reports/artifacts under
`children/index_options/pipeline_runs/exact_maturity_rms_20260928/`.
The candidate priority, payoff identity, and sensitivity bound are this
note's recommendations/derivations, rather than reported empirical results.
