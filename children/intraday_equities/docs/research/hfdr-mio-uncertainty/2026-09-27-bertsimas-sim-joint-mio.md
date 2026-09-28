# Question

For ADR-0188 formulation B, should Bertsimas-Sim uncertainty describe each
future realized return, or uncertainty in the name-by-exit-horizon conditional
mean? What is the exact robust counterpart of the scenario-utility MIO?

# Finding

Use a budgeted set over **mean-estimation error**. Estimate one interval for
each admitted `(name, exit horizon)` tranche, but spend the Bertsimas-Sim
budget over **names**, with all of a name's horizon errors moving in the same
adverse direction. Do not apply a second budgeted set to
the realized-return draws: the joint blocked residual scenarios and CVaR are
already the `U_r` mechanism. Treating each scenario return as another
Bertsimas-Sim coefficient would mix estimator uncertainty with outcome noise
and let nature choose a different mean error in each scenario.

For component `c=(i,k)`, estimate the mean label residual from strictly prior
out-of-fold rows with UTC trading sessions as the resampling unit. The shipped
`ClusterBootstrapInterval` is the existing whole-session bootstrap-t doorway
and returns the claim-bearing `ConfidenceInterval`; its center and asymmetric
endpoints are converted through the same label-to-simple-return map and
false-signal haircut as the scenario center. The release must carry an
attested named family of `ConfidenceInterval` values beside its existing
outcome and false-signal
artifacts. This is still developmental evidence: the current residual archive
does not include seed/refit/HPO variation, and the resulting per-cell coverage
has not yet been measured on a later untouched segment.

Let `e_ik >= 0` be target shares of name `i` allocated to exit-horizon
tranche `k`, `p_i` its decision price, and `d_ik` the adverse mean-return
deviation. A single mean-error vector applies to every
scenario, so it changes every scenario wealth by the same scalar. Because CRRA
utility is increasing, the worst expected utility spends the Bertsimas-Sim
budget on the largest name-level dollar effects. Its exact protection is

```
P(e) = min  Gamma * theta + sum_i rho_i
       s.t. theta + rho_i >= kappa_i * p_i * sum_k(d_ik * e_ik)
            theta, rho_i >= 0.
```

Budgeting per tranche would create an optimizer loophole: it could split one
name's economically identical share target fractionally across many correlated
exit horizons and dilute the protection whenever `Gamma` is smaller than the
tranche count. The name-level factor makes all of a name's horizon errors
comove at their own interval widths, while `Gamma` controls how many names may
be fully adverse together.

Every robust scenario-wealth identity therefore uses
`W_o = W_o_nominal - P(e)`. The same robust `W_o` feeds the tangent-CRRA and
CVaR rows. This is dimensionally consistent (return deviation times dollars of
exposure is dollars of wealth) and uses one shared protection block, not one
independent nature choice per scenario. Post-solve verification must recompute
`P(e)` independently with `BudgetedMeanSet.protection` from the solved tranche
allocation, then recompute robust wealth, utility, and CVaR. The tangent
envelope must include the robust downside too: bound each name's maximum
adverse dollar effect by its `x_max`, largest tranche deviation, and
`kappa_i`, then evaluate the same budgeted protection over those maxima and
subtract that bound from the envelope's lower edge. Protection cannot widen
the upper edge because it is non-negative. The existing one-percent-of-mark
positive lower bound is an explicit solvency constraint, not a nonbinding
numerical envelope, and must be reported beside realized robust wealth.

A mean confidence interval cannot be scored against the next session's mean:
that is predictive coverage for another estimand. Coverage evidence therefore
remains the existing known-true-mean Monte Carlo that makes
`ClusterBootstrapInterval` claim-bearing (nominal 0.95, measured 0.946--0.963
on genuinely independent whole units). A release separately reports each
cell's input-session count and refuses below an explicit minimum as unavailable;
that count is not coverage validation. Transfer of the synthetic method result
to cross-session equity residuals remains unvalidated developmental evidence.

`Gamma` and the `kappa_i` multipliers remain policy parameters, not statistical
consequences of the interval. Both must be explicit, default-free configuration
values. `kappa_i=1` reads each calibrated interval as supplied. `Gamma` must be chosen by
time-ordered validation of set violations and downstream utility, turnover,
concentration, drawdown, and cash/no-trade frequency; the folds later reported
as the end-to-end backtest must not also select it.

# Prior art and consequences

- ADR-0151 already supplies the mean-effect interval and explicitly leaves
  `Gamma` unidentified.
- ADR-0156 already supplies `BudgetedMeanSet`, including the exact
  Bertsimas-Sim counterpart description and independent post-solve protection
  calculation.
- `ForecastPublisher` already builds a complete-case residual panel across all
  path cells with session labels; `ForecastBundle` already owns conversion and
  scenario recentering. The new artifact should extend those seams rather than
  introduce a second residual reader or return conversion.
- The robust block belongs in generic `ScenarioUtilitySolve`; producing,
  attesting, and binding the mean intervals remains child-specific.
- The existing flat bundle/intake contract has exactly two uncertainty slots.
  The mean-family identity must therefore be path-only through a
  default-preserving intake hook; nominal flat consumers must not acquire a
  third required artifact.
- An empty bundle must never become a blanket liquidation authority. A held
  name whose screened plan is zero can instead carry a full, authenticated
  path row with `plan_horizon=0`; capital validates the usual digest,
  producer, model, cap, timestamp and uncertainty bindings and consumes that
  row only as evidence for a target-zero mandatory exit.
- The full folds 2--19 result is a developmental post-selection evaluation,
  not valid tuning evidence for `Gamma` or `kappa`.

# Sources

- Dimitris Bertsimas and Melvyn Sim (2004), *The Price of Robustness*,
  Operations Research 52(1):35--53. Theorem 1 gives the linear protection
  counterpart; section 6.2 applies coefficient uncertainty to expected returns
  in a portfolio objective. https://doi.org/10.1287/opre.1030.0065
- Author-hosted paper PDF:
  https://web.mit.edu/dbertsim/www/papers/melvyn/The-Price-Of-Robustness-OR52.pdf
- Repository ADR-0151, ADR-0156, and the existing research notes
  `hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md`,
  `hfdr-mio-uncertainty/2026-09-05-u-mu.md`, and
  `post-gate3-predictor-output/2026-09-05-robust-optimization-sets.md`.
