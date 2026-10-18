# Twenty new approaches with aligned historical development and validation

## Best candidate options

Pending execution. Candidates will be selected on warm-up alone before any new validation runs.

## Execution contract

The user requested 20 additional approaches and an apples-to-apples reassessment of all 37 previous experiments. Base commit: 93e85bd575c509e1fb3277a7cb5c32ee7615e98e. Scope: 57 methods, each with a development JSON and a frozen validation JSON, six complete output views per stage. No trading, deployment, new toolkit mechanism or protected forward-label read.

Development uses the earlier acquired history, with causal training and scoring February 19-April 19, 2026. The reader excludes May 2 onward. Models refit every 30 days using only settlement published plus one day strictly before each cutoff. Validation scores May 2-July 31 with one fit at May 2 and no later fitting. Candidate and all three fitted benchmarks share the same eligible cohort, cutoff and one-day embargo. Methods 33 and 34 therefore become duplicate schedule-normalized method05 controls; their earlier schedule distinctions are not tested in this aligned replay.

Development contains 52,999 forecasts / 11,213 contracts / 60 days. Validation contains 84,728 forecasts / 17,158 contracts / 91 days. Ten forecasts belonging to two contracts with decisions before the validation boundary are excluded by the frozen block. All comparisons include raw midpoint, per-lead logistic market recalibration, method05 ARD and method24 Newton ARD, freshly fitted under the matching schedule. The inherited GRU input has its original chronological training schedule; this study freezes residual/calibration models, not the inherited GRU backbone. Same GRU inputs for every candidate.

Both periods were previously inspected. The strict new workflow prevents additional validation-driven selection within this batch; it cannot erase prior knowledge or establish untouched confirmation. Earlier combined-period recommendations are superseded by this aligned assessment.

## Selection rule fixed before runs

After all 57 development results, shortlist at most three previous methods (excluding duplicate schedule-normalized 33/34) and three new methods. Eligibility requires negative mean contract-level log loss and Brier differences versus raw market. Rank eligible candidates by mean contract-level log-loss difference versus calibrated market, with method ID as tie-breaker. No validation result participates.

For each shortlisted method, select at most three fixed asset/lead/market-decile cells with at least 200 forecasts and 20 days, negative log loss versus calibrated market, and negative Brier versus raw market; rank by calibrated-market log-loss difference. Freeze these identities, configurations, development-result hashes and analysis rules before launching validation. All 57 validation methods still receive complete reports; unshortlisted winners are descriptive only.

Primary validation uses the frozen shortlist size as the global family and the frozen selected-cell count as the cell family, separately for each comparator. A candidate or cell passes a comparator only with negative mean log loss, one-sided UTC-day cluster t p-value adjusted below .05, and nonpositive Brier difference versus that same comparator. Fixed seven-day cluster sensitivity is also reported. The full 57-global / 5700-cell maps are exploratory. These corrections do not undo earlier adaptive historical reuse. No ranking changes or retuning after validation.

## Implementation status

All model mechanisms use existing generic pipeline nodes. Twenty new approaches test Bayesian/elastic/robust Newton surrogates, quadratic/spline/radial basis calibration, sparse interactions, three information ablations, volatility/spread experts, distance-weighted neighbors, supervised latent regression, three generative classifiers, histogram log loss, randomized Newton trees, and a two-layer residual network.

Artifacts: /home/russell/data/crypto_trading/research/warmup20-20261018.
