# Refining the empirical–MLP expiry-price CDF

## TL;DR

This bounded refinement is in progress; no new model result is claimed yet.
It tests whether changing the blend weight or its small neural component
improves on the existing 75% empirical / 25% MLP forecast. Reused historical
data can support a research comparison, not a fresh-data or trading guarantee.

## Execution contract

The owner asked to iterate on the changes that helped. The preceding study's
25% MLP blend improved equal-cell full-distribution CRPS by 0.818% versus
empirical on 2019–2025, but 8.039% of outcomes fell below its forecast 5th
percentile. It remains the incumbent research challenger, not a live model.
This refinement must demonstrate gain against that incumbent; beating the
weaker empirical reference alone is not further improvement.

The forecast is the signed raw-price log return from entry to actual listed
expiry settlement, not realized volatility. A cumulative distribution function
(CDF) gives the probability that the expiry price is below any chosen cutoff.
For cutoff K, entry spot S and causal reference scale a, query F(log(K/S)/a).
The blend averages probabilities: F = (1-w) F_empirical + w F_MLP.
The empirical component remains index/horizon-specific; the neural component
learns jointly from SPY, QQQ and IWM. This is a historical forecasting study,
not option selection, expected-profit optimization, execution or deployment.

### Thirteen predeclared candidates

- **Weight refinement:** original one-component, 16-unit MLP; weights
  0.15, 0.20, 0.30 and 0.35. The original weight 0.25 is a control.
- **Smaller MLP:** one component, 8 hidden units; weights 0.15, 0.25, 0.35.
- **Stronger regularization:** one component, 16 units, weight decay 0.3
  instead of 0.1; weights 0.15, 0.25, 0.35. Weight decay discourages large
  fitted weights; it is not the empirical/MLP blending weight.
- **More flexible distribution:** three Normal components per neural seed,
  16 hidden units; weights 0.15, 0.25, 0.35. This can represent unequal tail
  shapes without imposing one Normal curve; whether it helps is measured.

Other neural parameters stay fixed: tanh activation, 20 epochs, batches of
1,024, learning rate .003, minimum scale .1, no dropout, seeds [11,29], CUDA
and deterministic fitting. Unless explicitly changed above, weight decay is
.1. The two-seed ensemble therefore has two Normal terms for a one-component
model and six for a three-component model. Empirical curves retain 401 knots.

Controls are empirical, the unchanged pooled MLP, and the exact previous
25% blend. Calibration is disabled for the empirical and incumbent controls
to preserve their raw definitions. The existing API emits two identical
variant rows for each; they are one raw forecast, not evidence of a fitted
calibration improvement.
Other candidates retain the existing raw versus 21-knot calibration choices.

All four weight candidates share the incumbent's neural configuration and
fit-state equivalence label. Within each alternative architecture, its three
weights share an equivalence label too. This checks the same fitted neural
constituent during development. An alternative architecture's single final
winner has no separately fitted pure-MLP peer: its final singleton record is
honestly unverified, not a claim of independent endpoint certification.

### Training, selection and evaluation

For forecast year Y, training includes entry dates and settled labels strictly
before January 1 of Y-1. The preceding year supplies calibration rows, also
purged to labels strictly before January 1 of Y. Validation uses entries in Y.
Imputation and neural scaling use training rows only. Pooled models fit once
per year and weight rows equally; they do not give each index equal fit weight.

Development is 2016–2018 with every label settling before 2019-01-01.
One candidate and raw/calibrated variant per group freezes from development
CRPS at 101 numerical nodes. Settings and seeds do not change for evaluation.
Before reading any later evaluation, a single primary contender is selected
from those four group winners plus the raw incumbent by equal-cell relative
development CRPS against empirical. Exact ties prefer the incumbent, then
lexical model name. The source is the completed selection-stage development
scores; its hash and winner are recorded before further evaluation.
If the incumbent wins, the result is “no incremental challenger.” Other
finalists remain descriptive, not candidates for changing the primary claim
after seeing later outcomes.

Final scoring uses 401 nodes and the same 2019–2025 observations as previous
studies. These dates have repeatedly informed research and are not an untouched
holdout. Annual chronology does not remove this research-selection bias.
The expected cohorts are 18,616 development forecasts/133 observed cells and
68,084 evaluation forecasts/all 135 cells; new execution must verify them.

All learned models retain the same 42 causal inputs: 22 return lags; backward
realized volatility over 1/5/22/66 sessions; own VIX/VXN/RVX; planned calendar
and session tenor and logs; observed option-series age/total tenor and elapsed
life fractions; calendar-days-per-session; reference scale; and index one-hot
indicators. Neither feature selection nor index/tenor-dependent blend weights
are retuned here. Reporting uses actual settlement-day cells; features use
the calendar planned at entry rather than hindsight unscheduled closures.

### Interpreting improvement

For an index/day cell c, let M_c, B_c and I_c be mean CRPS for the proposed
model, empirical reference and incumbent respectively. Lower CRPS is better.
Skill versus empirical is 100 × mean_c(1-M_c/B_c); incremental skill versus
incumbent is 100 × mean_c(1-M_c/I_c). Both average the same cells equally,
not a ratio of pooled row means. Positive incremental skill is the relevant
comparison for this iteration.

Tail CRPS, cutoff-probability Brier score, 30–45-day results, observed 5th/95th
percentile exceedances and fixed synthetic-condor loss MSE are secondary.
No tail-specific fitting objective was added. Condor error is normalized by
wing size; it excludes initial option credits, spreads, fees, assignment,
dividends, early exits and execution, so it is not expected strategy return.
Numerical quadrature points are not independent observations.

Uncertainty retains 400 paired circular resamples of common entry-date blocks
of 60 and 120 dates. These preserve cross-index/horizon dependence within a
sampled date but remain pointwise, unadjusted research intervals; no family-wise
error budget or fresh-test guarantee is claimed. Thirteen candidates and two
calibration variants are 26 declared candidate/variant alternatives, plus
controls. Repeated study history increases the selection risk beyond this grid.

## Implementation and review

This is a JSON-only extension of existing capabilities under ADR-0191.
Sol owns the configuration, additive config tests and layout documentation;
Luna's design review cleared C0/M0 with a mixture-runtime caution. No
production source/dependency change or one-off execution script is planned.
The existing ADR-0191 evidence record retains the exact new protocol and
subsequent test/reviewer outputs. Final reviews and real execution are pending.

## Reproducibility and handoff

Base: abf360ca8b5fbb1b54382b2033df3809242ef43b. Worktree:
/home/russell/dskit-cdf-refinement-20260928. Use the existing child CLI with
configs/run-predictive-cdf-refinement.json and explicit search/select/evaluate/
report stages. New ignored outputs: pipeline_runs/predictive_cdf_refinement_20260928;
logs: pipeline_runs/cdf_refinement_logs_20260928. Previous outputs are preserved.

All computation is in WSL2. The prior task's private environment is reused
read-only, with the new checkout explicitly first on PYTHONPATH; imports were
verified to resolve to this checkout. Every stage uses a user-systemd scope
with 6-GiB host memory, no additional swap, two CPU cores and a 1,800-second
timeout; at most two concurrent stages. A tiny existing blend/multiyear-flow
probe passed two tests in 5.33 seconds, 6.76 process-wall seconds, peak RSS
1,011,628 KiB. This does not guarantee the full-panel resource budget.
Incomplete stages must be disclosed rather than quietly reducing the grid.
