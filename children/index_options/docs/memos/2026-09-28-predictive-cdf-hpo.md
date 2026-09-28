# Predictive price CDF: bounded HPO and pooled index heads

## TL;DR

All 24 JSON-declared candidates and the final two-seed evaluations completed;
tuning and pooling did not beat the historical empirical CDF overall. Pooled
MLP improved a fixed-condor loss-error diagnostic by 3.05%, but its full-CDF skill
was −1.92% and downside-tail calibration remained poor: retain it for research,
not trading or final-model promotion. Two independent Luna lenses are closed
with no unresolved Critical/Major findings.

## Execution contract

The question is whether a modest, predeclared architecture/training search and
sharing information across SPY, QQQ and IWM improve the full predictive return
curve. The target is the signed raw-price log return from entry close to actual
listed-expiry settlement, not realized volatility. For price cutoff K, entry
spot S and causal scale a, the forecast price CDF is F(log(K/S)/a). This answers
the probability of finishing below a strike; it does not price early assignment,
exits, spreads, fees, dividends or execution.

All experiments are declared in
`configs/run-predictive-cdf-hpo.json` and run through
`python -m index_options.cdf_study CONFIG --stage STAGE [--partition NAME]`.
No one-off training/execution script is used. The existing generic library owns
all fitting, scoring and HPO orchestration; the child adapter supplies the data
and bounded condor diagnostic. JSON stages are search (separate, pooled, heads),
select, evaluate (development, early, middle, late), and report. Each execution
partition has an external 1,800-second time cap. Partial directories cannot be
used as completed evidence; output overwrite is refused.

### Common inputs and candidates

All candidates and controls receive the same 42 inputs: 22 daily return lags;
1/5/22/66-session backward realized volatility; the relevant VIX/VXN/RVX value;
planned exact calendar/session tenor and their logs; observed series age and
total tenor in calendar/session units; elapsed-life fractions; calendar days per
session; the causal reference scale; and three index identity indicators.
Indicators are validated against the symbol before any missing-value imputation.
They are not learned or median-filled. Planned regular holidays determine model
tenor features; actual settlement dates determine labels, purges and report cells.
Future exceptional exchange closures are not supplied as predictors.

The 24 neural candidates are four bundles crossed with two distribution families
and three sharing structures. This is a bounded search, not an exhaustive claim
about all possible networks. Both families use bundle component counts 1/1/3/3.

| Bundle | Hidden widths | Epochs | Batch | Learning rate | Weight decay | Scale floor | Activation | Dropout | Student df |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| a | 16 | 20 | 1,024 | 0.003 | 0.1 | 0.1 | tanh | 0 | 8 |
| b | 32,16 | 60 | 512 | 0.001 | 0.1 | 0.05 | SiLU | 0 | 5 |
| c | 32,32 | 40 | 1,024 | 0.001 | 0.01 | 0.05 | tanh | 0 | 5 |
| d | 64,32,16 | 60 | 1,024 | 0.0003 | 0.1 | 0.1 | SiLU | 0.1 | 3 |

Separate models fit each index independently. Pooled-shared fits one model to
all index rows, with index indicators. Pooled-heads fits a shared trunk and
three separately routed output heads; only the row's corresponding head enters
its likelihood. Pooling is row-weighted, not equally weighted by index: SPY has
more rows. Pooling therefore changes the training population as well as sharing
architecture. It does not create three independent histories.

Controls are the exact-horizon empirical CDF, scaled empirical CDF, monotone
boosted CDF, quantile LightGBM, and the previous one-/three-component Gaussian
MLPs. They are outside the 24-candidate cap and repeat in each screening
partition. Their row-level outputs must agree exactly across partitions.

### Training, calibration, selection and evaluation

For each forecast year Y, training entries and their labels must both precede
January 1 of Y−1. Calibration entries are in Y−1 and all calibration labels
must settle before January 1 of Y. Forecast entries are in Y. A training-only
median imputer and neural scaler are fitted on the same permitted training
population. Pooled fitting happens once per model/year; calibration maps remain
index-specific. No early stopping or epoch choice reads the evaluation band.

Screening uses 2016–2018 forecasts whose labels also settle strictly before
2019-01-01. This extra selection cutoff removes late-2018 entries that would
otherwise reveal 2019 outcomes. There are 18,616 development rows: SPY 8,905,
QQQ 4,852, IWM 4,859. There are exactly 133 observed index/calendar-day cells:
SPY days 1–45 and QQQ/IWM days 1–45 except day 26. The two structural absences
are declared before tuning; no observation or score is invented for them.

Each screened candidate uses seed 11. One candidate and raw/calibrated variant
per sharing structure are selected by lowest equal-cell relative CRPS, then the
exact configurations and variants are frozen. Finalists use a fixed ensemble of
seeds 11 and 29, not the better seed. Control calibration choices also freeze
from development. Selection never reads evaluation score files. Final evaluation
has 68,084 rows: SPY 27,608, QQQ 21,493, IWM 18,983, covering all 135 cells.
These are reused 2019–2025 historical research years, NOT an untouched test.

CRPS measures error across the entire cumulative curve; lower is better. For
each index/day cell c, divide a model's mean CRPS by the empirical reference's
mean CRPS, then average these ratios equally across the declared cells. Skill
is 100 × (1 − mean ratio), in percent. Thus zero means matching the baseline;
negative means worse. The score uses standardized returns; raw-return CRPS,
tail calibration, strike probability error and bounded-condor loss error are
secondary checks. The 30–45-day region is secondary, not a post-hoc replacement
for the primary 1–45-day score.

Screening uses 101 quantile nodes; final scoring uses 401. Tail integration uses
201 nodes and the fixed synthetic condor uses 101 integration nodes per spread.
The report checks fixed saved-curve rows at 101/401/1,601 nodes. Numerical nodes
are not observations. Row counts, distinct entry dates, expiry series, fit/cal
bands and per-model pooled training counts are retained separately. Paired
date-block intervals retain all indexes/horizons together; they do not correct
for the whole model-search family or make the historical evaluation pristine.

## Are the distribution assumptions reasonable?

A finite Gaussian mixture can be skewed and multimodal; it is not necessarily
one symmetric bell. Its extreme log-return tails still decay as Gaussian tails.
Our pre-2019 descriptive audit shows heavy tails and asymmetry, particularly at
short horizons, but pooled moments do not identify the correct conditional law.
Financial-return literature motivates a sensitivity test, not a guarantee of
Student-t superiority. [Cont](https://rama.cont.perso.math.cnrs.fr/pdf/empirical.pdf),
[official ARCH examples](https://arch.readthedocs.io/en/latest/univariate/univariate_volatility_modeling.html#student-s-t-errors).

Student mixtures provide polynomial tails with fixed degrees of freedom above
two. Their scale is not standard deviation: SD = scale × sqrt(df/(df−2)). A
literal Student-t log return has no finite positive exponential moment, so it
cannot support finite unbounded stock/call expectations. A condor payoff is
bounded, and its expectation can be computed by finite-interval CDF integrals.
We only evaluate those bounded quantities. Extreme quadrature draws are clamped
to the outer strikes before exponentiation solely because the wing payoff is
constant beyond those strikes; the CDF itself is not truncated.
[Student-t density](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html).

Empirical and quantile curves have finite-endpoint assumptions too. The causal
square-root-session scale is a normalization, not a fixed prediction of future
volatility; the model can adjust its shape and scale with horizon/features.
Nevertheless, normalization affects how observations are weighted in scoring.
Full assumptions research, descriptive counts and sources are in
`docs/research/distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo.md`.

## Screening results (completed)

All 24 predeclared candidates completed. Every raw/calibrated candidate below
has 18,616 development observations and the same 133 declared cells. Higher
skill is better; every neural candidate here is worse than the empirical
reference on development. Raw beat calibrated for every candidate. No fitted
Student family or deeper bundle won its sharing structure.

| Candidate | Raw development skill | Calibrated development skill |
|---|---:|---:|
| separate_normal_a | -2.368% | -13.986% |
| separate_normal_b | -16.295% | -30.912% |
| separate_normal_c | -9.598% | -22.874% |
| separate_normal_d | -4.963% | -19.969% |
| separate_student_a | -3.730% | -15.548% |
| separate_student_b | -18.183% | -34.886% |
| separate_student_c | -8.473% | -21.962% |
| separate_student_d | -5.292% | -20.347% |
| pooled_normal_a | -5.532% | -17.141% |
| pooled_normal_b | -10.078% | -25.170% |
| pooled_normal_c | -9.164% | -24.219% |
| pooled_normal_d | -7.100% | -22.454% |
| pooled_student_a | -6.063% | -16.883% |
| pooled_student_b | -21.873% | -36.227% |
| pooled_student_c | -8.632% | -23.170% |
| pooled_student_d | -6.451% | -21.546% |
| heads_normal_a | -5.073% | -18.075% |
| heads_normal_b | -12.319% | -27.337% |
| heads_normal_c | -11.338% | -23.396% |
| heads_normal_d | -7.644% | -22.745% |
| heads_student_a | -5.335% | -18.248% |
| heads_student_b | -14.297% | -29.234% |
| heads_student_c | -9.708% | -23.453% |
| heads_student_d | -7.886% | -23.567% |

Selected separately in each sharing group: `separate_normal_a`,
`pooled_normal_a`, `heads_normal_a`, all raw. This does not mean three overall
winners or a final model promotion. Each has one hidden layer of 16 tanh units
and one Gaussian component per network, averaged across two fixed seeds at
final evaluation. The separate model fits independently by index; pooled-shared
uses one network per seed; pooled-heads has one shared trunk and three routed
three-parameter output blocks. With one component the weight logit is redundant:
each seed effectively predicts location and positive scale. The two-seed curve
is an equal-weight two-Gaussian mixture, not merely a single average volatility.
There are 739 trainable parameters per separate/shared network (42→16→3),
or 841 for a three-head network (42→16→9). Per year, the separate-index ensemble
fits six networks totaling 4,434 parameters; each pooled alternative fits two
networks totaling 1,478 or 1,682. These are small models, not large language
models or a model for each strike. One row's curve can answer every strike cutoff
for that entry and expiry. Calibration remains a separate held-out mapping,
but development selected the raw curves in this run.

Selection saved 18,616 exact expected forecast identities, hash
`507ce0ed39300fb1062b5762395e9c6b1f7923273a55341e6c4dd89c67c26948`. Screen-spec hash:
`3156e6d638b64ad7b20f2f18de57fe76524cb160e31ab0e4fb2fb9bf4758caca`; final-spec hash:
`e26a6d719daa39cb94045fde4bdf5b13d442fb1ae21621fb11ace17aff0e146e`. Seeds change from [11] to
[11,29] by predeclared rule, not by comparing seed scores.

## Final research-evaluation results

All nine models below have exactly **68,084 paired observations**, 1,746 distinct
entry dates and 3,495 index/nominal-expiry series in 2019–2025. Each index has 45
actual-calendar-day cells. SPY: 27,608 rows / 1,745 dates / 1,383 series; QQQ:
21,493 / 1,745 / 1,146; IWM: 18,983 / 1,742 / 966. Overlapping windows and common
market shocks mean these rows are not independent observations.

| Model | Mean standardized CRPS | Equal-cell skill | SPY skill | QQQ skill | IWM skill |
|---|---:|---:|---:|---:|---:|
| Horizon empirical | 0.606705 | +0.00% | +0.00% | +0.00% | +0.00% |
| Monotone CDF | 0.607193 | -0.91% | +0.07% | +0.46% | -3.26% |
| Original MLP-1 architecture | 0.611458 | -1.58% | -1.59% | -1.56% | -1.60% |
| Pooled MLP | 0.612008 | -1.92% | -0.57% | -1.54% | -3.65% |
| Separate MLP | 0.613146 | -1.97% | -1.42% | -2.31% | -2.17% |
| Pooled heads | 0.613793 | -2.11% | -1.48% | -2.26% | -2.61% |
| Original MLP-3 architecture | 0.613249 | -2.51% | +0.04% | -1.59% | -5.97% |
| Quantile LightGBM | 0.616163 | -3.10% | -1.68% | -1.64% | -5.97% |
| Scaled empirical | 0.624370 | -3.33% | -4.20% | -4.40% | -1.39% |

For the best tuned finalist, the mean cellwise candidate/reference ratio is
1.0191716226. Skill = 100 × (1 − 1.0191716226) = **−1.9172%**. Thus pooled MLP
has about 1.92% MORE full-CDF error by the predeclared primary measure. Its
advantage over separate MLP is only 0.049 percentage points; separate heads
do not help. None of the tuned finalists beats the empirical reference overall
or on any individual index. The original architecture controls are not bitwise
replicas of the prior 39-input experiment: all now receive 42 inputs, including
index indicators. Even constant per-index inputs change neural initialization
dimensions, so fixed seeds do not preserve the old trained weights. The
empirical and boosted control results remain numerically aligned
with the prior evaluation; do not cherry-pick the older favorable MLP result.

### Paired uncertainty, not a new-data guarantee

Pointwise 95% percentile intervals use 400 resamples of common entry-date blocks,
with all indexes/horizons carried together. There is no multiple-search correction
and this is already-inspected history. Each interval below uses 68,084 paired
rows / 1,746 dates; intervals crossing zero do not establish positive skill.

| Finalist | Skill | 60-date-block interval | 120-date-block interval |
|---|---:|---:|---:|
| Separate MLP | -1.97% | [-4.50%, +0.58%] | [-4.31%, +1.04%] |
| Pooled MLP | -1.92% | [-4.58%, +0.98%] | [-4.46%, +1.09%] |
| Pooled heads | -2.11% | [-4.81%, +0.72%] | [-4.71%, +0.77%] |

### The 30–45-day region

These are 16 declared day cells per index, not a substitute primary objective.
The same paired sample counts apply to each model in a row.

| Index | Observations | Separate full-CDF skill | Pooled full-CDF skill | Heads full-CDF skill |
|---|---:|---:|---:|---:|
| SPY | 6,262 | -3.62% | -2.50% | -3.79% |
| QQQ | 4,902 | -3.38% | -3.11% | -2.72% |
| IWM | 4,621 | -2.69% | -4.85% | -3.11% |

### Bounded payoff and tails: a narrower positive, not a strategy edge

The fixed synthetic condor has outer/inner standardized log strikes −2,−1,+1,+2.
Its settlement loss is bounded. Squared expected-loss error is normalized by the
wider wing squared; lower is better. It excludes entry credit, costs, contract
selection, assignment and early exits. This is not realized strategy return.
All entries below use 68,084 evaluation rows and equal-cell relative scoring.

| Finalist | Tail-CRPS skill | Strike-probability skill | Bounded-loss MSE | Bounded-loss MSE skill |
|---|---:|---:|---:|---:|
| Separate MLP | -0.37% | -0.16% | 0.077793 | +0.43% |
| Pooled MLP | -0.19% | +0.16% | 0.076357 | +3.05% |
| Pooled heads | -0.48% | -0.23% | 0.077680 | +0.99% |

Reference mean bounded-loss MSE is 0.080635. Pooled MLP's equal-cell loss-error
skill is **+3.05%**, a legitimate secondary lead worth retaining. This report
does not provide paired uncertainty intervals for that secondary MSE, so it
does not establish that the improvement is stable. In the 30–45-day region its
loss-error skill is SPY -0.71%, QQQ +3.68%, IWM -3.39%.
The positive overall diagnostic therefore does not translate into a consistent
improvement across the particular expiry region of interest.

A calibrated 5% lower tail should contain approximately 5% of outcomes. The
following rates are pooled row frequencies, with exact event counts; dependence
precludes treating the denominator as that many independent trials.

| Forecast | Below predicted 5th percentile | Above predicted 95th percentile | Central 90% coverage |
|---|---:|---:|---:|
| Horizon empirical | 7.94% (5,405/68,084) | 9.58% (6,525/68,084) | 82.48% |
| Separate MLP | 10.15% (6,911/68,084) | 4.08% (2,779/68,084) | 85.77% |
| Pooled MLP | 10.24% (6,970/68,084) | 3.45% (2,348/68,084) | 86.31% |
| Pooled heads | 10.33% (7,035/68,084) | 3.71% (2,528/68,084) | 85.95% |

The pooled model's downside tail is materially too optimistic: 10.24% of
outcomes fall below its supposed 5th percentile. The empirical baseline also
has imperfect coverage. Neither is a reliably calibrated probability engine
for automatically selecting supposedly optimal live condors.

## Complete exact-day finalist grid

Each number is 2019–2025 equal-cell CRPS skill versus horizon empirical; each row
is one cell, so it is the relative mean CRPS in that index/day. N is the number
of paired forecasts for EACH model, not summed across models or quadrature nodes.
All control-model and secondary-metric grids are retained in
`report/skill_by_exact_day.csv` (not omitted from the underlying report).

### SPY

| Actual days | N per model | Separate | Pooled | Heads |
|---:|---:|---:|---:|---:|
| 1 | 1004 | +0.48% | +0.36% | +0.78% |
| 2 | 831 | +0.31% | +0.79% | +0.73% |
| 3 | 808 | +3.09% | +2.76% | +2.64% |
| 4 | 807 | +3.32% | +4.05% | +3.11% |
| 5 | 842 | +2.24% | +2.34% | +1.69% |
| 6 | 1006 | +0.77% | +0.94% | +0.07% |
| 7 | 1320 | +1.14% | +1.60% | +1.24% |
| 8 | 1019 | -0.06% | +0.30% | -0.22% |
| 9 | 827 | +0.87% | +0.81% | +0.90% |
| 10 | 805 | +0.44% | +0.53% | +0.54% |
| 11 | 805 | +0.87% | +1.48% | +1.17% |
| 12 | 839 | +2.48% | +2.20% | +2.45% |
| 13 | 1001 | +0.96% | +0.89% | +0.50% |
| 14 | 1290 | +0.40% | +0.77% | +0.45% |
| 15 | 591 | -1.86% | +0.09% | -1.44% |
| 16 | 547 | -0.88% | +0.52% | -0.88% |
| 17 | 544 | -1.24% | -0.24% | -0.94% |
| 18 | 520 | -2.03% | -0.66% | -1.41% |
| 19 | 410 | -0.44% | +0.76% | +0.20% |
| 20 | 429 | -0.30% | +1.01% | -0.22% |
| 21 | 752 | -1.47% | -0.42% | -1.05% |
| 22 | 582 | -2.52% | -1.20% | -2.59% |
| 23 | 546 | -1.66% | -0.69% | -1.90% |
| 24 | 543 | -1.60% | -0.60% | -1.84% |
| 25 | 518 | -2.05% | -0.94% | -2.38% |
| 26 | 407 | -0.21% | +0.95% | -0.12% |
| 27 | 426 | -1.14% | +0.13% | -1.31% |
| 28 | 748 | -2.50% | -1.63% | -2.34% |
| 29 | 579 | -3.52% | -2.40% | -3.79% |
| 30 | 543 | -3.05% | -1.88% | -3.31% |
| 31 | 539 | -1.46% | -1.12% | -1.34% |
| 32 | 517 | -2.60% | -1.37% | -2.66% |
| 33 | 403 | -1.43% | -0.27% | -1.30% |
| 34 | 427 | -2.63% | -1.65% | -2.41% |
| 35 | 746 | -2.97% | -2.09% | -2.88% |
| 36 | 576 | -3.67% | -3.02% | -3.43% |
| 37 | 368 | -3.04% | -2.84% | -4.63% |
| 38 | 536 | -2.76% | -1.92% | -2.44% |
| 39 | 348 | -2.24% | -2.48% | -2.56% |
| 40 | 23 | -10.24% | -6.20% | -8.25% |
| 41 | 40 | -6.71% | -3.48% | -7.48% |
| 42 | 370 | -3.63% | -3.69% | -4.22% |
| 43 | 364 | -4.75% | -3.54% | -4.86% |
| 44 | 236 | -3.60% | -2.04% | -4.94% |
| 45 | 226 | -3.07% | -2.35% | -3.91% |

### QQQ

| Actual days | N per model | Separate | Pooled | Heads |
|---:|---:|---:|---:|---:|
| 1 | 887 | -0.43% | -0.70% | -1.47% |
| 2 | 724 | -0.49% | +0.56% | -0.80% |
| 3 | 705 | +0.38% | +2.20% | +0.47% |
| 4 | 689 | +0.33% | +2.77% | +1.00% |
| 5 | 613 | +3.79% | +4.61% | +2.77% |
| 6 | 774 | +1.83% | +2.94% | +1.26% |
| 7 | 1095 | -0.86% | +0.68% | -0.81% |
| 8 | 893 | -1.24% | -0.77% | -2.30% |
| 9 | 720 | -1.04% | -0.51% | -1.28% |
| 10 | 703 | -2.17% | -0.84% | -2.06% |
| 11 | 687 | -1.89% | -0.23% | -1.62% |
| 12 | 610 | +6.49% | +8.38% | +6.04% |
| 13 | 769 | +2.27% | +3.85% | +2.24% |
| 14 | 1064 | -1.48% | +0.23% | -1.70% |
| 15 | 461 | -3.06% | -2.58% | -4.19% |
| 16 | 438 | -2.72% | -1.88% | -4.13% |
| 17 | 438 | -3.55% | -2.64% | -4.16% |
| 18 | 400 | -5.12% | -3.69% | -4.64% |
| 19 | 174 | -0.60% | +2.36% | -1.71% |
| 20 | 190 | -2.12% | -1.95% | -1.68% |
| 21 | 521 | -3.89% | -3.65% | -3.98% |
| 22 | 452 | -4.94% | -3.90% | -5.18% |
| 23 | 434 | -4.01% | -3.28% | -3.74% |
| 24 | 435 | -3.66% | -2.77% | -4.15% |
| 25 | 397 | -5.64% | -4.85% | -5.37% |
| 26 | 171 | -3.13% | -1.33% | -3.81% |
| 27 | 185 | -3.56% | -3.51% | -3.58% |
| 28 | 511 | -4.08% | -4.53% | -4.02% |
| 29 | 451 | -5.32% | -4.41% | -5.40% |
| 30 | 432 | -4.88% | -3.91% | -4.73% |
| 31 | 431 | -3.80% | -3.34% | -2.74% |
| 32 | 396 | -5.90% | -5.43% | -4.56% |
| 33 | 167 | -0.69% | +1.78% | -0.02% |
| 34 | 185 | -5.39% | -6.27% | -3.94% |
| 35 | 513 | -5.49% | -5.03% | -4.11% |
| 36 | 444 | -4.96% | -5.67% | -4.50% |
| 37 | 362 | -3.44% | -2.39% | -3.52% |
| 38 | 426 | -4.44% | -4.00% | -3.12% |
| 39 | 332 | -5.72% | -5.73% | -3.80% |
| 40 | 16 | +0.72% | +0.55% | +1.69% |
| 41 | 31 | +2.89% | +0.83% | +1.05% |
| 42 | 361 | -4.15% | -4.86% | -3.10% |
| 43 | 355 | -3.93% | -4.97% | -3.77% |
| 44 | 229 | -1.84% | +0.57% | -1.71% |
| 45 | 222 | -3.07% | -1.84% | -2.66% |

### IWM

| Actual days | N per model | Separate | Pooled | Heads |
|---:|---:|---:|---:|---:|
| 1 | 730 | +0.62% | +0.10% | +0.47% |
| 2 | 635 | +0.98% | -0.27% | +0.37% |
| 3 | 625 | +0.18% | +0.18% | -0.13% |
| 4 | 604 | +0.19% | +1.18% | +0.63% |
| 5 | 506 | -0.62% | -1.03% | -0.36% |
| 6 | 598 | -0.36% | -0.56% | -0.18% |
| 7 | 922 | -0.15% | -0.98% | -0.21% |
| 8 | 742 | -0.03% | -1.06% | -0.36% |
| 9 | 631 | +0.86% | -0.49% | +0.72% |
| 10 | 623 | -0.85% | -2.89% | -1.08% |
| 11 | 600 | -0.06% | -1.02% | +0.20% |
| 12 | 501 | +0.38% | +0.20% | +1.20% |
| 13 | 590 | -0.75% | -1.06% | -0.46% |
| 14 | 899 | -1.07% | -1.95% | -0.98% |
| 15 | 429 | -0.83% | -2.30% | -1.98% |
| 16 | 414 | +0.35% | -0.73% | -0.09% |
| 17 | 416 | +0.46% | -1.59% | -0.04% |
| 18 | 376 | -0.39% | -0.84% | -0.60% |
| 19 | 127 | -13.97% | -17.43% | -15.10% |
| 20 | 142 | -8.91% | -9.73% | -11.06% |
| 21 | 475 | -1.32% | -2.38% | -1.74% |
| 22 | 424 | -1.40% | -3.02% | -2.16% |
| 23 | 411 | +0.51% | -0.81% | +0.11% |
| 24 | 413 | +0.82% | -1.07% | -0.21% |
| 25 | 373 | +0.71% | -0.60% | -0.11% |
| 26 | 125 | -13.28% | -15.24% | -14.05% |
| 27 | 140 | -14.43% | -15.62% | -16.54% |
| 28 | 467 | -1.60% | -2.93% | -1.53% |
| 29 | 424 | -0.43% | -2.70% | -2.16% |
| 30 | 409 | -0.06% | -1.92% | -1.06% |
| 31 | 410 | +0.55% | -1.96% | -0.16% |
| 32 | 371 | -0.60% | -2.32% | -1.12% |
| 33 | 121 | -22.11% | -25.88% | -22.09% |
| 34 | 139 | -14.98% | -19.64% | -16.33% |
| 35 | 468 | -2.49% | -3.46% | -2.09% |
| 36 | 419 | -0.76% | -3.21% | -1.59% |
| 37 | 358 | +1.07% | -0.67% | +0.81% |
| 38 | 405 | -0.40% | -2.83% | -0.33% |
| 39 | 326 | +0.53% | -1.15% | +0.48% |
| 40 | 12 | -2.46% | -3.47% | -3.05% |
| 41 | 27 | -1.16% | -3.26% | -2.81% |
| 42 | 359 | -0.30% | -1.86% | -0.04% |
| 43 | 352 | -0.18% | -2.08% | -0.03% |
| 44 | 225 | +0.05% | -2.10% | -0.25% |
| 45 | 220 | +0.25% | -1.79% | -0.17% |

## Training, calibration and validation counts

The rows below show the separate-index bands. F/C/V are training, calibration
and validation counts; the following three columns are distinct entry dates in
those bands. Years 2016–2018 are strictly pre-2019 development; 2019–2025 are
research evaluation. Both label-end purges were checked in every recorded fold.
Pooled models use the combined training rows shown separately below, fit once
per year; their probability calibration still uses each index's own C band.

| Index | Forecast year | F rows | C rows | V rows | F dates | C dates | V dates |
|---|---:|---:|---:|---:|---:|---:|---:|
| IWM | 2016 | 6188 | 1635 | 1689 | 1760 | 251 | 252 |
| IWM | 2017 | 7931 | 1603 | 1620 | 2012 | 251 | 251 |
| IWM | 2018 | 9616 | 1534 | 1550 | 2264 | 250 | 249 |
| IWM | 2019 | 11236 | 1550 | 1669 | 2515 | 249 | 250 |
| IWM | 2020 | 12872 | 1570 | 1717 | 2765 | 249 | 252 |
| IWM | 2021 | 14533 | 1635 | 2334 | 3015 | 251 | 250 |
| IWM | 2022 | 16267 | 2106 | 4077 | 3267 | 249 | 251 |
| IWM | 2023 | 18455 | 3972 | 2592 | 3517 | 250 | 250 |
| IWM | 2024 | 22655 | 2484 | 3303 | 3768 | 249 | 251 |
| IWM | 2025 | 25244 | 3174 | 3291 | 4018 | 250 | 238 |
| QQQ | 2016 | 4384 | 1631 | 1682 | 950 | 250 | 251 |
| QQQ | 2017 | 6123 | 1596 | 1620 | 1201 | 250 | 251 |
| QQQ | 2018 | 7799 | 1534 | 1550 | 1452 | 250 | 249 |
| QQQ | 2019 | 9419 | 1550 | 1677 | 1703 | 249 | 251 |
| QQQ | 2020 | 11055 | 1578 | 1717 | 1953 | 250 | 252 |
| QQQ | 2021 | 12724 | 1635 | 3506 | 2204 | 251 | 251 |
| QQQ | 2022 | 14458 | 3278 | 4173 | 2456 | 250 | 251 |
| QQQ | 2023 | 17818 | 4058 | 3495 | 2707 | 250 | 250 |
| QQQ | 2024 | 22104 | 3376 | 3591 | 2958 | 249 | 252 |
| QQQ | 2025 | 25595 | 3462 | 3334 | 3208 | 251 | 238 |
| SPY | 2016 | 6205 | 1646 | 2139 | 1761 | 251 | 252 |
| SPY | 2017 | 7959 | 1987 | 2931 | 2013 | 251 | 251 |
| SPY | 2018 | 10028 | 2779 | 3835 | 2265 | 250 | 249 |
| SPY | 2019 | 12959 | 3835 | 4246 | 2516 | 249 | 251 |
| SPY | 2020 | 16946 | 4035 | 4316 | 2766 | 250 | 252 |
| SPY | 2021 | 21197 | 4101 | 4296 | 3017 | 251 | 252 |
| SPY | 2022 | 25509 | 4067 | 4230 | 3269 | 251 | 251 |
| SPY | 2023 | 29791 | 4115 | 3505 | 3521 | 250 | 250 |
| SPY | 2024 | 34135 | 3376 | 3691 | 3772 | 249 | 252 |
| SPY | 2025 | 37626 | 3562 | 3324 | 4022 | 251 | 237 |

Pooled training rows per year (one fit per model, not repeated per reporting
index): 2016: 16,777; 2017: 22,013; 2018: 27,443; 2019: 33,614; 2020: 40,873; 2021: 48,454; 2022: 56,234; 2023: 66,064; 2024: 78,894; 2025: 88,465.

Full first/last entry dates, latest label dates, expiry-series counts, model
fit counts, fit timings and per-seed final training likelihoods are retained
in each `evaluate/<partition>/counts.json`. The model-specific `new_fit` flag
distinguishes a real pooled fit from its reuse while reporting another index.

## Runtime, numerical verification and limits

Every declared stage completed with exit status zero; none was skipped, resumed,
timed out or silently narrowed. At most two independent compute partitions ran
concurrently. No CPU fallback or package/dependency change was made.

| Stage | Wall time | Peak host RSS (GiB) | Exit |
|---|---:|---:|---:|
| evaluate-development | 3:07.25 | 2.46 | 0 |
| evaluate-early | 4:41.22 | 2.63 | 0 |
| evaluate-late | 4:29.55 | 2.90 | 0 |
| evaluate-middle | 4:13.75 | 2.77 | 0 |
| report | 0:28.85 | 1.42 | 0 |
| search-heads | 5:52.12 | 2.30 | 0 |
| search-pooled | 6:04.06 | 2.39 | 0 |
| search-separate | 5:59.93 | 2.54 | 0 |
| select | 0:13.09 | 0.99 | 0 |

CUDA fitting ran on the WSL-accessible RTX 5060 Ti (16 GiB); CDF integration and
tabular scoring run on CPU. The maximum per-process host RSS was 2.90 GiB.
The recorded Torch version is 2.11.0+cu128. Repeated screening controls agreed
exactly across all three partitions, but this is not a cross-device bitwise
determinism certification. Runtime determinism settings and library versions
are preserved in the partition artifacts.

The report reconstructed 216 final-evaluation curve files, including both raw
and calibrated variants, and audited 12 fixed rows/file at 101/401/1,601 nodes.
Across those samples, 401-vs-1,601 mean CRPS differed by at most 0.00004518
(0.00663% relative). Tail quadrature at 401-vs-1,601 nodes differed by at most
0.00041453; the production research tail score itself used the declared 201
nodes, so that comparison is not an exact 201-node error certificate. The
largest audited 401-node payoff-versus-CDF integration gap was $0.018852/share.
The audit concerns final models and controls, not every screened Student fit.
Student numerical family/inverse/likelihood and extreme bounded-payoff behavior
were covered by focused tests; no fitted Student model survived selection.

No interruption/crash/restart experiment, full test suite, new archive acquisition,
American-option execution test, fresh untouched holdout or live trading was
performed. Source and both review lenses are unchanged; the final report verifies
configuration/panel/provenance/implementation/score/selection hashes and exact
paired forecasts before completion.

## Recommendation

Keep the empirical horizon-specific distribution as the full-CDF reference.
This is a historical distribution—not an average-only prediction. The bounded
search did not establish that deeper MLPs, Student mixtures or separate index
heads improve the full curve. It also does not prove returns are Gaussian:
these are finite families, fixed-degree alternatives and four deliberately
limited training bundles, with a single screening seed and only three
development forecast years.

Retain the small pooled MLP as a **secondary bounded-payoff research candidate**
because of its +3.05% overall loss-error result, without promoting it to a final
model. The next useful experiment would predeclare the relevant strike/tenor
loss objective, compare against the empirical curve (including a conservative
blend), and validate downside-tail calibration on fresh data. Do not simply
run a larger search on the same inspected years. A practical strike optimizer
should wait for stable, calibrated payoff evidence and actual quote/cost/
assignment handling; the present results do not justify one.

## Implementation evidence versus empirical results

Reviewed implementation: `f396baca6b17e69a3ec44924c05bca220f250807`, based on
`86447b4d`. Focused curve/model/adapter and pipeline-purity checks: 65 passed;
ruff and whitespace checks passed. Luna Phase 0 closed after explicit leakage,
identity, pooling, resolution and bounded-payoff contracts. Both independent
final Luna lenses completed on this same immutable implementation. The first
raised one Major about fixed cardinalities in a reusable JSON-configured runner;
fresh independent integration/adjudication withdrew it after verifying different
configurations cannot reuse this experiment's hash-pinned artifacts. No source,
test or contract was changed to evade the finding. Actual reports and adjudication
are retained in `docs/review-evidence/ADR-0190.md` at the repository root.

All three search partitions, selection, four evaluation partitions and report
completed successfully. This new experiment is the relevant evidence for the
declared 42-input comparison, not a claim that its architecture controls exactly
reproduce earlier neural initializations with 39 inputs.

## Reproducibility and handoff

Canonical configuration hash at preflight:
`b808b80e4a7fa69873f18a0b73c16d30d34d9b1e50a470e76651b8d3143745bc`.
Canonical 109,355-row panel-value hash:
`e24b1de89b4af4cb48651f2189930ad826ad96f955b57a8b5e8f142bf26fce2f`.
The configuration names local source archive and cached expiry metadata paths.
Artifacts bind canonical config, panel values, upstream file/reader fingerprints,
implementation hashes, score files and frozen selection. Completion markers
are written atomically last. A timeout is an incomplete run, not a losing model.

Artifact root (preserved locally, intentionally ignored by Git):
`/home/russell/dskit-cdf-hpo-20260928/children/index_options/pipeline_runs/predictive_cdf_hpo_20260928`.
Logs: sibling `pipeline_runs/cdf_hpo_logs_20260928`. In the artifact root,
`selection/selected.json` freezes specifications/variants; `report/comparison.json`
holds rankings and paired intervals; `report/skill_by_exact_day.csv` holds all
model/metric grids and counts; `report/scores.parquet` retains 1,560,600 score
rows (86,700 identities × 9 models × 2 variants); partition `counts.json` and
saved NPZ files retain fold accounting and exact forecast curves. Reproduce
through the nine documented CLI stages in the child README using the WSL
environment and source/configuration identities above, with a new output root.

Research execution, memo and independent code reviews are complete. No model
promotion, broker integration, live trade or optimizer deployment is authorized
by these results. The worktree is preserved for its ignored research artifacts;
the wrap record in `docs/RE-ENTRY.md` identifies what was merged/pushed.
