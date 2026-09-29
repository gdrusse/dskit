# Refining the empirical–MLP expiry-price CDF

## TL;DR

The preselected smaller 35% MLP blend achieved 0.965% skill versus empirical,
up from 0.818% for the old blend, but its direct 0.150% incremental gain is not
convincing: paired intervals cross zero and downside misses worsen. Keep the
old blend as the research benchmark; reused history does not justify trading.

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

The pre-evaluation winner is **blend_small_035, raw**: 65% empirical / 35%
MLP, one Normal component per seed and 8 hidden units. Its development
equal-cell relative CRPS is 0.9871675354795064, or 1.283246% skill versus
empirical. Other group winners are mixture .35 (1.258231%), regularized .35
(1.106624%), and original-network weight .30 (1.024166%); the incumbent has
1.001381%. These are selection results, not later-period evidence.
All 13 candidates completed; no new trial was added. This primary and the
complete contender ranking are committed in ADR-0191 evidence while the
evaluation directory is still absent. Frozen selected.json SHA-256:
`8bc93005e21db40623411108b20f985a7c96f73eede4ba544a151ef949e53902`;
development score source SHA-256:
`2d01497a96d08dabfb639a17d936a39cfedd8479a9b2bab02350ac532dd4c56d`.

### What the frozen model actually looks like

Each of two seeded networks takes the same 42 features, after training-only
imputation and standardization, through one 8-unit tanh hidden layer. Each
predicts a mean and positive scale for a Normal standardized terminal return.
The implementation also has a one-component mixture logit; its single
component probability is necessarily one. No separate index heads are used.

For standardized return z, its raw forecast is
`F(z) = .65 F_empirical(z) + .175 Phi((z-mu_11)/sigma_11) + .175 Phi((z-mu_29)/sigma_29)`,
where Phi is the standard Normal CDF and the
subscripts identify fixed seeds, not selected winners. The empirical curve
has 401 stored knots for the entry's index and planned horizon. Thus the final
curve is not just a Normal distribution despite each seed predicting one.
For an expiry-price cutoff K, substitute z = log(K/S)/a, where S is entry
spot and a is the causal reference scale. Saved exact-curve artifacts retain
the empirical knots and both neural Normal terms for numerical reconstruction.

This contender changes both network size and blend weight relative to the
incumbent. An improvement would support this combined specification, not
prove that shrinking the network alone caused it. The search was bounded;
winning at the .35 edge does not establish a global optimum or authorize
extending the grid after seeing evaluation.

A reconstructed historical example: SPY entered 2025-11-12 at $683.38,
expiring/settling 2025-12-12 (30 actual and planned calendar days), has reference
scale 0.032892473. This curve assigns 9.838% probability below $649.211,
37.936% below $683.38, and 86.698% below $717.549. Its forecast 5th/50th/95th
expiry-price percentiles are $637.24 / $691.67 / $734.08. These are outputs
of a historical forecast, not validated coverage guarantees or quoted options.

Final scoring uses 401 nodes and the same 2019–2025 observations as previous
studies. These dates have repeatedly informed research and are not an untouched
holdout. Annual chronology does not remove this research-selection bias.
The verified cohorts are 18,616 development forecasts/133 observed cells and
68,084 evaluation forecasts/all 135 cells.

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

## Completed results

All 13 development candidates, selection, four frozen evaluation partitions
and the final evaluator completed successfully; zero failed, omitted or resumed
stages. Final results contain **1,213,800 score rows** across seven models and
two emitted variants, not 1.2 million independent observations. Each model has
18,616 development forecasts and **68,084 later-period forecasts** on 1,746
entry dates and 3,495 nominal expiry series. Development has 133 observed cells
(QQQ/IWM day 26 absent); evaluation has all 135 index/day cells. All selected
variants are raw. Overlapping expiries and shared market dates make these
forecasts dependent.

### Main comparison: 2019–2025

CRPS measures the error of the whole forecast distribution; lower is better.
The mean below is row-weighted and measured in standardized-return units.
Skill columns give equal weight to each index/day cell, so they are not
computed by taking ratios of the displayed pooled means. Every row uses the
same 68,084 forecasts. “vs old” means the unchanged 25% blend.

| Model | Mean CRPS | Skill vs empirical % | Skill vs old % | SPY vs empirical % | QQQ vs empirical % | IWM vs empirical % |
|---|---:|---:|---:|---:|---:|---:|
| Empirical reference | 0.606705 | +0.000 | -0.837 | +0.000 | +0.000 | +0.000 |
| Old 25% blend | 0.600837 | +0.818 | +0.000 | +0.966 | +1.227 | +0.261 |
| **Primary: small network, 35%** | 0.599726 | +0.965 | +0.150 | +1.151 | +1.378 | +0.365 |
| Original network, 30% blend | 0.600239 | +0.878 | +0.063 | +1.071 | +1.343 | +0.219 |
| Stronger regularization, 35% | 0.599312 | +0.986 | +0.173 | +1.251 | +1.429 | +0.276 |
| Three-component network, 35% | 0.598919 | +1.029 | +0.220 | +1.666 | +1.792 | -0.372 |
| Pure original MLP | 0.612008 | -1.917 | -2.733 | -0.565 | -1.536 | -3.650 |

The primary improves full-range skill from 0.818035% to 0.964721% versus
empirical. Its direct incremental score is **+0.150304% versus the old blend**;
this is not the 0.146687 percentage-point subtraction between empirical skills.
For example, its pooled mean falls from 0.600837267 to 0.599725623, but that
pooled ratio is not the equal-cell statistic used to choose or assess it.

The primary beats empirical in **115/135 cells**, versus 123/135 for the old
blend, and beats the old blend in **100/135 cells**. A higher average did not
mean improvement everywhere. The three-component finalist has the best later
aggregate point estimate, but was not the frozen primary; its IWM score is
negative. It remains descriptive, not a post-hoc replacement winner.

### How convincing is the incremental gain?

| Primary comparison | Point skill % | 95% interval, 60-date blocks | 95% interval, 120-date blocks |
|---|---:|---:|---:|
| vs empirical | +0.965 | [+0.074, +1.876] | [+0.250, +1.866] |
| vs old 25% blend | +0.150 | [-0.101, +0.407] | [-0.085, +0.396] |

Both incremental intervals cross zero. This is a small favorable point estimate,
not convincing evidence that the new specification is better than the old one.
The nominal positive intervals versus empirical do not remove repeated-history
selection bias. The descriptive mixture's incremental intervals also cross
zero: [-0.469%, +1.009%] and [-0.567%, +1.054%]. No multiplicity-adjusted or
fresh-test significance claim is made.

### Primary by index and region of interest

| Index | Forecasts | Entry dates | Expiry series | Full-range vs empirical % | Full-range vs old % | 30–45-day forecasts | 30–45 vs empirical % | 30–45 vs old % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SPY | 27,608 | 1745 | 1383 | +1.151 | +0.188 | 6,262 | +0.610 | +0.091 |
| QQQ | 21,493 | 1745 | 1146 | +1.378 | +0.158 | 4,902 | +1.170 | +0.216 |
| IWM | 18,983 | 1742 | 966 | +0.365 | +0.106 | 4,621 | -0.022 | +0.019 |

The 30–45-day region contains **15,785 forecasts / 48 cells**. Primary skill
there is +0.585990% versus empirical and +0.108654% versus the old blend.
IWM remains slightly below empirical in this region (-0.021892%). No
index-specific model/weight switch was selected after seeing these values.

### Secondary scores and probability calibration

| Primary metric | Raw mean | Skill vs empirical % | Skill vs old % |
|---|---:|---:|---:|
| Tail CRPS | 0.33724805 | +1.527 | +0.303 |
| Raw log-return CRPS | 0.02388705 | +1.062 | +0.177 |
| Cutoff-probability Brier | 0.08142816 | +1.553 | +0.287 |
| Normalized condor-loss MSE | 0.07698643 | +3.661 | +0.784 |

The condor-loss MSE improvement is +0.783924% relative to the incumbent,
not an expected-return gain. It assesses a fixed synthetic strike geometry,
not optimally selected quoted options.

| Model | Below forecast 5th percentile | Above forecast 95th percentile | Inside nominal 90% interval |
|---|---:|---:|---:|
| Empirical reference | 5,405/68,084 (7.939%) | 6,525/68,084 (9.584%) | 82.478% |
| Old 25% blend | 5,473/68,084 (8.039%) | 4,075/68,084 (5.985%) | 85.976% |
| **Primary: small network, 35%** | 5,669/68,084 (8.326%) | 3,252/68,084 (4.776%) | 86.897% |
| Three-component network, 35% | 5,323/68,084 (7.818%) | 4,117/68,084 (6.047%) | 86.135% |

The primary's **downside miss rate worsens from 8.039% to 8.326%** when it
should be about 5%. Upside coverage and overall central coverage improve, but
that does not repair downside underestimation. The mixture is less bad on
this downside diagnostic (7.818%), yet still substantially miscalibrated and
weaker on IWM. These are empirical rates on dependent forecasts, not independent
binomial trials or guaranteed future coverage.

### Full primary exact-day grid

Each cell is **N forecasts; skill vs empirical % / skill vs old %**. These are
later-period validation results, never training scores. Day means actual
calendar days from entry to label settlement; causal model features retain the
planned schedule. Negative values mean worse than the named reference.

| Actual days | SPY: N; empirical / old | QQQ: N; empirical / old | IWM: N; empirical / old |
|---:|---:|---:|---:|
| 1 | 1004; +1.432 / +0.452 | 887; +1.228 / +0.415 | 730; +1.274 / +0.229 |
| 2 | 831; +1.357 / +0.178 | 724; +1.390 / +0.259 | 635; +0.967 / +0.045 |
| 3 | 808; +2.862 / +0.736 | 705; +2.019 / +0.186 | 625; +1.657 / +0.443 |
| 4 | 807; +2.722 / +0.406 | 689; +2.229 / +0.278 | 604; +1.734 / +0.391 |
| 5 | 842; +1.857 / +0.278 | 613; +3.717 / +0.824 | 506; +1.651 / +0.389 |
| 6 | 1006; +1.439 / +0.255 | 774; +2.326 / +0.515 | 598; +1.051 / +0.315 |
| 7 | 1320; +1.571 / +0.264 | 1095; +1.377 / +0.190 | 922; +0.739 / +0.116 |
| 8 | 1019; +1.406 / +0.373 | 893; +1.341 / +0.473 | 742; +0.946 / +0.326 |
| 9 | 827; +1.618 / +0.435 | 720; +1.346 / +0.405 | 631; +0.984 / +0.210 |
| 10 | 805; +1.505 / +0.379 | 703; +1.135 / +0.251 | 623; +0.613 / +0.418 |
| 11 | 805; +1.714 / +0.284 | 687; +1.237 / +0.101 | 600; +1.083 / +0.434 |
| 12 | 839; +2.111 / +0.501 | 610; +6.057 / +1.324 | 501; +2.670 / +0.529 |
| 13 | 1001; +1.504 / +0.184 | 769; +3.394 / +0.610 | 590; +1.247 / +0.156 |
| 14 | 1290; +1.385 / +0.193 | 1064; +1.255 / +0.031 | 899; +0.532 / +0.128 |
| 15 | 591; +1.161 / +0.037 | 461; +0.318 / -0.256 | 429; +0.479 / +0.081 |
| 16 | 547; +1.314 / +0.077 | 438; +0.457 / -0.241 | 414; +0.601 / -0.162 |
| 17 | 544; +1.082 / +0.067 | 438; +0.275 / -0.253 | 416; +0.760 / +0.112 |
| 18 | 520; +1.224 / +0.279 | 400; +0.037 / -0.217 | 376; +0.826 / +0.081 |
| 19 | 410; +1.539 / +0.217 | 174; +3.964 / +0.038 | 127; -1.746 / +0.211 |
| 20 | 429; +1.840 / +0.369 | 190; +2.568 / +0.536 | 142; -0.953 / -0.077 |
| 21 | 752; +1.156 / +0.168 | 521; -0.017 / -0.393 | 475; +0.668 / +0.295 |
| 22 | 582; +0.855 / -0.013 | 452; -0.046 / -0.333 | 424; +0.474 / +0.192 |
| 23 | 546; +1.196 / +0.225 | 434; +0.140 / -0.235 | 411; +0.708 / -0.030 |
| 24 | 543; +0.998 / +0.071 | 435; +0.324 / -0.148 | 413; +0.641 / -0.128 |
| 25 | 518; +1.038 / +0.113 | 397; -0.306 / -0.359 | 373; +1.060 / +0.232 |
| 26 | 407; +1.528 / +0.228 | 171; +3.347 / +0.159 | 125; -1.608 / +0.210 |
| 27 | 426; +1.131 / +0.020 | 185; +2.542 / +0.252 | 140; -3.192 / -1.053 |
| 28 | 748; +0.856 / +0.144 | 511; -0.195 / -0.360 | 467; +0.527 / +0.290 |
| 29 | 579; +0.660 / +0.063 | 451; -0.166 / -0.404 | 424; +0.360 / +0.059 |
| 30 | 543; +0.845 / +0.144 | 432; -0.091 / -0.344 | 409; +0.273 / -0.219 |
| 31 | 539; +1.099 / +0.279 | 431; +0.333 / -0.093 | 410; +0.553 / +0.007 |
| 32 | 517; +0.991 / +0.216 | 396; -0.243 / -0.186 | 371; +0.481 / +0.006 |
| 33 | 403; +1.158 / +0.106 | 167; +7.984 / +1.414 | 121; -3.525 / -0.506 |
| 34 | 427; +0.951 / +0.194 | 185; +1.984 / +0.178 | 139; -3.067 / -0.180 |
| 35 | 746; +0.703 / +0.094 | 513; -0.119 / -0.255 | 468; +0.179 / +0.009 |
| 36 | 576; +0.722 / +0.265 | 444; -0.062 / -0.031 | 419; +0.317 / +0.097 |
| 37 | 368; +0.626 / +0.233 | 362; +0.434 / -0.138 | 358; +0.627 / -0.165 |
| 38 | 536; +0.779 / +0.154 | 426; +0.348 / +0.036 | 405; +0.518 / +0.132 |
| 39 | 348; +1.086 / +0.533 | 332; -0.227 / -0.007 | 326; +0.800 / +0.073 |
| 40 | 23; -1.164 / -0.767 | 16; +2.747 / +1.506 | 12; -0.113 / +0.297 |
| 41 | 40; -0.397 / -0.525 | 31; +3.126 / +1.542 | 27; -0.274 / -0.121 |
| 42 | 370; +0.528 / +0.328 | 361; +0.409 / +0.208 | 359; +0.873 / +0.353 |
| 43 | 364; +0.457 / +0.111 | 355; +0.141 / -0.013 | 352; +0.790 / +0.304 |
| 44 | 236; +0.766 / +0.136 | 229; +1.171 / -0.264 | 225; +0.527 / +0.086 |
| 45 | 226; +0.604 / -0.043 | 222; +0.790 / -0.104 | 220; +0.691 / +0.134 |

Days 40–41 have only 12–40 forecasts per index/day, versus up to 1,320
elsewhere. Equal-cell scoring still gives each cell the same weight; it is
not precision-weighted. Do not treat a large percentage in a thin cell as a
reliable local trading signal. No cell-by-cell confidence claim is made.

### Actual annual training/calibration/validation populations

Pooled training counts are the observations used once per model/year by each
neural model and its blended empirical component. The standalone empirical
control uses that index's training count. Each index cell below is
**training / calibration-band / validation N**; “calibration” denotes the
available held-out band, not a fitted calibration for the two disabled controls.
The raw selected variants do not apply a calibration map. Validation values
include all declared forecasts, with the extra pre-2019 label cutoff applied
to development. No in-sample training score is presented as model skill.

| Forecast year | Pooled training N | SPY: train / cal / val | QQQ: train / cal / val | IWM: train / cal / val |
|---:|---:|---:|---:|---:|
| 2016 | 16,777 | 6,205 / 1,646 / 2,139 | 4,384 / 1,631 / 1,682 | 6,188 / 1,635 / 1,689 |
| 2017 | 22,013 | 7,959 / 1,987 / 2,931 | 6,123 / 1,596 / 1,620 | 7,931 / 1,603 / 1,620 |
| 2018 | 27,443 | 10,028 / 2,779 / 3,835 | 7,799 / 1,534 / 1,550 | 9,616 / 1,534 / 1,550 |
| 2019 | 33,614 | 12,959 / 3,835 / 4,246 | 9,419 / 1,550 / 1,677 | 11,236 / 1,550 / 1,669 |
| 2020 | 40,873 | 16,946 / 4,035 / 4,316 | 11,055 / 1,578 / 1,717 | 12,872 / 1,570 / 1,717 |
| 2021 | 48,454 | 21,197 / 4,101 / 4,296 | 12,724 / 1,635 / 3,506 | 14,533 / 1,635 / 2,334 |
| 2022 | 56,234 | 25,509 / 4,067 / 4,230 | 14,458 / 3,278 / 4,173 | 16,267 / 2,106 / 4,077 |
| 2023 | 66,064 | 29,791 / 4,115 / 3,505 | 17,818 / 4,058 / 3,495 | 18,455 / 3,972 / 2,592 |
| 2024 | 78,894 | 34,135 / 3,376 / 3,691 | 22,104 / 3,376 / 3,591 | 22,655 / 2,484 / 3,303 |
| 2025 | 88,465 | 37,626 / 3,562 / 3,324 | 25,595 / 3,462 / 3,334 | 25,244 / 3,174 / 3,291 |

### Recommendation

Keep the original 25% blend as the incumbent research benchmark and carry the
small 35% blend as a modest, predeclared alternative. **Do not call this a
clear model upgrade or promote it to trading.** Its incremental uncertainty
includes no improvement, fewer cells beat empirical, and downside coverage
gets worse despite better average scores.

The useful finding is that modest neural contributions remain more effective
than the standalone original MLP. More distributional flexibility improved the
descriptive SPY/QQQ average but hurt IWM. The next useful experiment should be
predeclared on genuinely new dates, with downside/cutoff calibration assessed
alongside average score. No new-data collection, tail-loss implementation,
additional search, index-specific switching or deployment was started here.

## Implementation and review

This is a JSON-only extension of existing capabilities under ADR-0191.
Sol owns the configuration, additive config tests and layout documentation;
Luna's design review cleared C0/M0 with a mixture-runtime caution. No
production source/dependency change or one-off execution script was needed.
The existing ADR-0191 evidence record retains the exact new protocol and
subsequent test/reviewer outputs. Final candidate is
`88f85a8b601861e2794039775c54ef996bbf3910`; two fresh Luna lenses completed,
with zero unresolved Critical/Major after fresh independent adjudication.
The integration lens disputed whether the primary winner needed an API guard;
the adjudicator confirmed the agreed parent-owned, pre-evaluation evidence
commit satisfies the protocol. This external step is not machine-enforced by
the generic CLI. Its test-automation gap remains a Minor, alongside the
inherited negligible convex-inverse atom rounding issue. No post-lock code
change followed. Seven child tests and 13 reused grouped-flow/pairing/hash/
equivalence tests passed; no full suite was run.

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
No stage was incomplete or quietly narrowed.

### Completed execution and numerical checks

All ten stages exited zero. The first search ran alone; later independent
partitions used at most two concurrent processes. Peak process RSS was
**2.644 GiB** and the slowest stage was **9:39.82**, both below the enforced
6-GiB / 30-minute per-stage bounds. The final evaluator took 31.19 seconds.
These are process measurements, not a claim about total machine memory.

| Stage | Wall time | Peak RSS KiB |
|---|---:|---:|
| search-weight | 2:23.98 | 2462704 |
| search-small | 2:10.09 | 2406368 |
| search-regularized | 2:09.37 | 2410160 |
| search-mixture | 2:38.87 | 2511896 |
| select | 0:07.37 | 707500 |
| evaluate-development | 6:32.66 | 2656540 |
| evaluate-early | 9:39.82 | 2729004 |
| evaluate-middle | 8:32.56 | 2772952 |
| evaluate-late | 8:19.65 | 2756380 |
| report | 0:31.19 | 1236976 |

The evaluator reconstructed 168 saved curves and checked 12 rows each
(**2,016 row/curve checks**) at 101/401/1,601 numerical nodes. Maximum
401-versus-1,601 mean CRPS gap was 0.000022926 standardized units, or
0.0033814% relative; the analogous maximum tail-score relative difference
was 0.128033%. Maximum 1,601-node payoff-vs-CDF integration discrepancy on
these audited rows was $0.00341534/share. These are sample numerical checks,
not exhaustive error bounds or additional independent observations.

Luna independently validated all completed inventories/hashes, selection
binding, paired identities, purges and pooled-fit reuse. Representative
SPY primary saved curves from the middle/late partitions reproduce stored
PIT (CDF at the realized outcome) exactly, with maximum difference zero;
quantile-to-CDF discrepancy was at most 0.000856 in its probe. Alternative
architecture singleton equivalence records remain explicitly unverified.
Both parent and Luna independently reproduced the old raw controls exactly.

The source implementation SHA-256 is
6e4e84df05cdb83fe01aed4d739d3881b9db937e3036e3f88384e3e9e7b52932;
canonical panel identity is
e24b1de89b4af4cb48651f2189930ad826ad96f955b57a8b5e8f142bf26fce2f.
The JSON file SHA-256 is
7375d6f996478cafd021d32af534d39c9c6bb5b0ab3e352fe33b778c3518a786;
its canonical configuration identity is
36010b4ba21633809b71d968e2c61b4bc27597ac4c1693ed59d7b73905366cc1.
These are different byte/canonical identities, not conflicting digests.

Final report SHA-256 values:

- comparison.json: `a6a6e27496bad971ac673e68a8a848301c0275756a07b590410f9ca8c0a041e4`.
- scores.parquet: `537b1b2d4fee2d324b4a17639ab93dab1cd65169920387c74bf26c771895a8c1`.
- convergence.json: `5839c836161565ea0f5cfb5534a1fea17a44f44b7b8b7c16c9e1a77b169a4e72`.
- complete.json: `7b48435988900039541277f23380c7f8f507624ef732f06427f05092401ee0ce`.
- skill_by_exact_day.csv: `8730d335e66dde2cc9acad18d39031cf01f63014ff6113aeab52de0223896426`.

From the child directory, the standard command is:

```text
python -m index_options.cdf_study configs/run-predictive-cdf-refinement.json --stage search --partition weight
```

Repeat search with small, regularized and mixture; then --stage select.
Compute and commit the declared primary freeze before evaluation. Run
--stage evaluate with development, early, middle and late, then --stage report.
Use a fresh output location for a new experiment; completed directories are
hash-bound evidence, not disposable caches. The actual WSL command prefix was:

```text
systemd-run --user --scope --quiet -p MemoryMax=6G -p MemorySwapMax=0 -p CPUQuota=200%
env PYTHONPATH=/home/russell/dskit-cdf-refinement-20260928:/home/russell/dskit-cdf-refinement-20260928/children/index_options
CUBLAS_WORKSPACE_CONFIG=:4096:8 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
/usr/bin/time -v timeout 1800 /home/russell/dskit-cdf-methods-20260928/.venv/bin/python
```

Join the prefix lines with spaces and append the standard module arguments.
Versions: Python 3.12.3, NumPy 2.5.2, pandas 2.3.3, SciPy 1.18.1,
scikit-learn 1.9.0, Torch 2.11.0+cu128 / CUDA 12.8, RTX 5060 Ti.
Neural fitting uses explicit deterministic CUDA contexts. The versions file's
process-global deterministic flags are captured outside those contexts and
can be false; actual fit-state equivalence/control reproduction is retained.
No environment was changed. Optional tree libraries installed for the previous
study are not used in this refinement.

The primary freeze was committed as af23efa06a72aaa60589105226a50d4e1d036ede
before any evaluation; the final model/variant has not changed. All outputs
and logs remain ignored locally in the named worktree. Previous methods/HPO
and feature-research worktrees and their data remain intact. Publication is
being completed under the owner's existing wrap/merge/push authorization;
no live model, trade, deployment, additional search or fresh-data validation.
