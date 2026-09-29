# Testing wider neural spreads and index-specific heads

## TL;DR

This bounded iteration is being prepared; no new empirical result is claimed.
It tests existing scale-floor and index-head settings while keeping the
empirical baseline and original25%blend fixed. A development downside guard
will accompany average-score selection; reused historical data is not a fresh
holdout or trading validation.

## Execution contract

The previous round's small35%blend gained only0.150% against the incumbent,
with paired intervals crossing zero and downside misses rising from8.039% to
8.326%. This round asks whether a minimum neural spread or index-specific
outputs can improve that tradeoff. It does not add features, invent a tail loss,
change data, or optimize option trades.

The target remains entry-to-listed-expiry-settlement raw log-price return,
not realized volatility. The output is a continuous cumulative distribution
function (CDF): a probability for any expiry-price cutoff. The empirical/MLP
blend combines probabilities, not point forecasts.

### Nine declared candidates, same data and features

- Six scale-floor candidates: .5/.75/1.0 in normalized-return units, each
  at 25%/35% neural weight. One Normal component and one 16-unit hidden layer.
- Three index-head candidates: shared16-unit trunk with separate SPY/QQQ/IWM
  output heads, original minimum scale.1, weights15%/25%/35%.
- All other settings stay fixed: tanh, weight decay.1,20epochs,batch1024,
  learning rate.003, no dropout, seeds[11,29], deterministicCUDA. Same42inputs,
  original raw empirical and25%blend controls, and original pureMLP control.

The scale is softplus(network output) plus its configured floor. A larger
floor is a symmetric spread constraint, not a downside-specific loss or a
promise that every refitted forecast widens. The target is raw log return
divided by the causal reference scale; it is not centered or z-scored to unit
sample variance. Features alone receive training-fitted standardization.
For one Normal component the minimum raw log-return scale is floor times
reference_scale, not a fixed percentage annual volatility. Heads are separate outputs of
one pooled model, not three independently fitted models. Pooled likelihood
weights observations, not indexes, equally.

Training for yearY uses entries and settled labels before January1 ofY-1;
calibration uses the preceding year with labels before January1 ofY. All
imputation/scaling is training-only. Development is2016–2018 with all labels
before2019; later research evaluation is2019–2025. Reporting uses actual days;
features use the schedule planned at entry. Dates have already informed
research; chronological fitting does not eliminate experiment-selection bias.

Existing JSON-driven selection picks one candidate/raw-or-calibrated variant
per group using101-node equal-cell CRPS (whole-distribution error; lower better).
The parent then commits a primary before ANY evaluation. A finalist qualifies
only if its development CRPS beats incumbent AND, in each index, its absolute
below-5% miss-rate deviation from 5% does not worsen (tolerance 1e-12). The
event is strictly PIT < .05, where PIT is the CDF at the realized normalized
return. Each index rate is its event count divided by its observation count,
using exactly paired candidate/incumbent rows, not equal-cell averaging. Among
qualifiers choose lowest relativeCRPS; ties incumbent first then name. Otherwise
retain incumbent and report no qualified challenger. This parent-owned record
is not an automated genericAPI enforcement mechanism.

All two group finalists plus three controls still receive401-node scoring,
with no changed settings/seeds. Other finalists are descriptive. The guard
does not claim global constrained optimality or guaranteed later calibration.
It is another development-reused selection step, not independent validation.
Fit-state equivalence labels certify identical neural constituents for the
different blend weights, not identical final probability curves; final
alternative-architecture singletons remain explicitly unverified.
No extra trial, changed threshold or post-evaluation model switch is allowed.

## Implementation and verification

Sol builds the JSON and focused configuration tests; Luna performs design and
independent final reviews. Existing generic scale/head/blend mechanisms are
reused without source or dependency changes. Precise protocol, actors, matrix,
review evidence and eventual pre-evaluation freeze are appended toADR-0191.
No one-off execution script, shared-environment mutation or full test suite.
Phase0 Luna review cleared C0/M0 with the scale, strict-rate and fit-state
clarifications above. Final reviews and real-data stages remain pending.

## Reproducibility and handoff

Base969fd3e48be10e13313e08a6a14eee47de49664b; worktree
/home/russell/dskit-cdf-downside-20260928. Planned standardCLI document:
configs/run-predictive-cdf-downside.json. Fresh ignored output
pipeline_runs/predictive_cdf_downside_20260928 and logs
pipeline_runs/cdf_downside_logs_20260928. Prior studies remain untouched.
Reuse prior private interpreter read-only with this checkout first onPYTHONPATH.
WSL2/CUDA; each stage capped at1800seconds/6GiB/no additionalswap/twoCPUs,
at most two concurrent, first search alone. No new run has started yet.
