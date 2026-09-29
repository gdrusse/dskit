# Testing wider neural spreads and index-specific heads

## TL;DR

Wider neural floors improve historical distribution skill from 0.818% to
0.977% versus empirical, but the extra 0.164% over our incumbent is uncertain
and downside misses worsen. Neither challenger passed the predeclared guard;
keep the original 25% MLP blend as a research benchmark, not a trading model.

## Execution contract

The owner asked for another iteration on the empirical/MLP distribution model.
The preceding smaller-network experiment gained only 0.150% against the
incumbent, with paired uncertainty intervals crossing zero, while downside
misses rose from 8.039% to 8.326%. This round asks whether a minimum neural
spread or separate index outputs can improve that tradeoff. It does not change
features or data, invent a new training loss, select options, or authorize trading.

The target is entry-to-listed-expiry-settlement **raw log-price return**, not
realized volatility. For entry spot S, terminal price T and causal reference
scale a, the modeled outcome is y = log(T/S)/a. The reference is
a = max(trailing 22-session daily realized volatility, .001) × sqrt(planned
sessions remaining). It rescales the target; it does not center it or make its
sample standard deviation one.

The output is a cumulative distribution function (CDF): for an expiry-price
cutoff K, query F(log(K/S)/a). The blend combines probabilities:
F = (1-w) F_empirical + w F_MLP. The empirical component uses historical
raw-return quantiles for the same index and planned calendar horizon, with
401 knots; it is not merely an average return. The neural part learns from all
three indexes. For the one-component/two-seed settings here, that neural part
is the average of two fitted Normal distributions, not one Normal assumption
for the whole blended curve.

### Nine predeclared candidates

- Six scale-floor candidates: minimum scales .5, .75 and 1.0 in normalized
  return units, each at 25% and 35% neural weight. One 16-unit hidden layer.
- Three index-head candidates: shared 16-unit trunk with separate SPY, QQQ and
  IWM output heads, original minimum scale .1, weights 15%, 25% and 35%.
- All other neural settings stay fixed: one component per seed, tanh,
  weight decay .1, 20 epochs, batches of 1,024, learning rate .003, no dropout,
  seeds [11,29], deterministic CUDA fitting.

The scale is softplus(network output) plus the configured floor. A larger
floor is a symmetric spread constraint, not a downside-specific objective or
a promise that every refitted forecast widens. Minimum raw log-return scale
is floor × a, not a fixed annual volatility. Heads are outputs of one pooled
model, not three independently trained models. Pooled likelihood weights
observations equally, so indexes with more rows contribute more training weight.

Controls are the raw empirical model, unchanged numerical 25% blend, and
original pooled pure MLP. Calibration is disabled for empirical and incumbent;
the API emits identical raw/calibrated copies for them, not two fitted methods.
Other models may use their raw or 21-knot recalibrated CDF. The base pair's
equivalence metadata was renamed from pooled_normal_a_endpoint to base_pure_inc;
numerical definitions are unchanged, but the control objects are not byte-identical.
Exact raw-control numerical parity with the previous search was verified.

### Same 42 inputs and data

Inputs are 22 daily return lags; trailing realized volatility over 1, 5, 22 and
66 sessions; the index's VIX/VXN/RVX close; calendar/session time remaining and
their logarithms; observed series age and total observed tenor in calendar
days/sessions; corresponding lifecycle fractions; calendar days per session;
the causal reference scale; and three index indicators. Observed first-seen
age is archive-observed age, not a claim about the contract's original listing.

Index/horizon/price inputs come from the existing local options-history
archive and exact-expiry surface/lifecycle files; implied volatility comes
from the pinned Cboe series. No acquisition or feature selection was repeated.
Missing implied-volatility values use training-only median imputation.
Model-facing horizons use the schedule planned at entry; actual settlement
horizons are label/report metadata. Nominal expiry remains part of forecast
identity, including different nominal expiries that share a settlement day.

### Training, selection and evaluation

For forecast year Y, training uses entries AND settled labels strictly before
January 1 of Y-1. Calibration uses the preceding year's entries and only labels
settled strictly before January 1 of Y. Validation uses entries in Y.
Imputation and neural standardization fit training rows only. Pooled neural
models fit once per year and reuse that fit across indexes; index calibration
uses only that index's purged calibration data.

Development covers 2016–2018, with all development labels before 2019-01-01.
Later research evaluation covers 2019–2025. These dates have already informed
research: chronological fitting does not make them a fresh, untouched holdout.

The existing JSON selector freezes one candidate and raw/calibrated variant
per group by 101-node development CRPS, a whole-distribution error score
(lower is better). If c indexes an index/actual-day cell, define
R = mean_c(mean CRPS_candidate,c / mean CRPS_empirical,c).
Each cell has equal weight, regardless of its number of observations.
Reported skill against empirical is 100 × (1-R), in percent.

Before ANY evaluation, the parent applies the declared downside guard to the
two group winners, using exactly paired development rows. A challenger must
have strictly lower R than the incumbent and, for EACH index:
abs(n_down/N - .05) <= abs(n_down_incumbent/N - .05) + 1e-12.
Here N is that index's observation count and n_down counts strict PIT < .05;
PIT is the forecast CDF evaluated at the actual outcome. Rates are
observation-weighted within index, not equal-cell means. Absolute value measures
distance from the desired 5% event rate, not absolute returns.

Among eligible contenders choose lowest R; exact ties prefer incumbent, then
lexical model name. If none qualifies, retain incumbent. This external recorded
procedure is not a newly machine-enforced generic API gate. It acts after
groupwise CRPS selection, so it is not a globally optimal constrained search
over all losing candidates. It also cannot guarantee later calibration.

### Frozen development decision

**Primary: incumbent_blend_025, raw — no qualified challenger.**
Freeze commit: 793bcd8998635f36d99b67b4666aae202bdf6559, made while the
evaluation directory was absent. All five final variants are raw.

| Contender | Development R | Skill vs empirical | Guard result |
|---|---:|---:|---|
| Floor .75 / MLP 35% | 0.9839420929 | +1.605791% | Fails SPY |
| Index heads / MLP 25% | 0.9895235466 | +1.047645% | Fails SPY and QQQ |
| Incumbent / MLP 25% | 0.9899861919 | +1.001381% | Retained |

| Index | Development N | Incumbent below-5% | Floor finalist | Head finalist |
|---|---:|---:|---:|---:|
| SPY | 8,905 | 525 / 5.895564% | 526 / 5.906794% | 529 / 5.940483% |
| QQQ | 4,852 | 313 / 6.450948% | 306 / 6.306678% | 321 / 6.615829% |
| IWM | 4,859 | 237 / 4.877547% | 242 / 4.980449% | 241 / 4.959868% |

For the floor finalist, SPY's deviation is abs(526/8905-.05) =
.0090679394 versus incumbent .0089556429: one event worse, exceeding the
1e-12 numerical tolerance. We do not loosen that rule after observing it.
This strict procedural failure is not evidence of a statistically meaningful
SPY difference. Both challengers move IWM closer to 5%; only floor improves QQQ.

All nine candidates completed; full raw/calibrated development ranks are in
the ADR-0191 evidence record. Group winners plus all three controls receive
401-node final scoring with unchanged settings/seeds. Other finalists remain
descriptive; there is no post-evaluation primary substitution or extra trial.

## Implementation and verification

Sol implemented one JSON and an additive configuration test. Existing generic
scale/head/blend mechanisms were reused without source or dependency changes.
Luna design review cleared the normalized-scale and guard definitions, followed
by independent correctness and integration reviews of immutable 257eb591.
Both final lenses had zero Critical/Major findings; the focused child suite
passed 8 tests with 15 known dependency warnings. No full repository suite ran.

Two new Minors are retained: the disclosed control-equivalence label rename,
and the test's lack of exhaustive shared-MLP-field pins (current full candidate
dictionaries were independently checked). The inherited negligible convex
inverse atom issue and external primary-freeze automation gap remain deferred.
No post-lock numerical/source/config changes were made to fix nits.

Within each floor-weight pair and head-weight triplet, equivalence checks
certify identical fitted neural constituents, not identical blended curves.
Final alternative singletons lack a peer and must remain explicitly unverified.
The base pure-MLP/incumbent pair provides a verified endpoint comparison.

Luna's completed artifact audits verified every recorded hash in both searches,
selection, all four evaluations and report; exact forecast pairing; both annual
purges; pooled fit reuse; and honest equivalence status. Representative saved
SPY floor/head curves, raw and recalibrated, reproduce stored PIT exactly using
their component state and saved calibration maps. No new findings. These are
bounded checks, not a full archive reacquisition or production safety review.

## Empirical results: 2019–2025 research evaluation

All eight declared CLI stages completed with exit 0: two searches, selection,
four evaluations, and the final evaluator/report. No failed, skipped, resumed,
silently dropped or CPU-fallback stage. The report contains 867,000 score rows:
five models × two variants × 86,700 forecasts across development and evaluation.
Those variant/model copies do not multiply the amount of underlying data.

Every later comparison below uses exactly the same **68,084 forecasts** across
135 cells, 1,746 entry dates and 3,495 nominal-expiry series. These are dependent,
overlapping observations, not 68,084 independent experiments. Development uses
18,616 forecasts and 133 cells; QQQ/IWM actual day 26 is absent there but present
in later evaluation. No later cell was dropped.

### Whole-distribution score

Mean CRPS is observation-weighted in normalized-return units. Skill is
equal-cell percentage error reduction; it is not obtained by dividing the
pooled means in the second column. Positive skill means lower error.

| Model, all raw | Mean CRPS | Skill vs empirical % | Skill vs incumbent % | Positive cells vs empirical / 135 |
|---|---:|---:|---:|---:|
| Empirical reference | 0.606704770 | +0.000000 | -0.837467 | 0 |
| Incumbent 25% (primary) | 0.600837267 | +0.818035 | +0.000000 | 123 |
| Floor .75 / 35% (descriptive) | 0.600089447 | +0.977245 | +0.164269 | 127 |
| Heads / 25% (descriptive) | 0.601073533 | +0.793238 | -0.026627 | 126 |
| Pure pooled MLP | 0.612007975 | -1.917162 | -2.733414 | 38 |

The floor finalist's average cell ratio against empirical is .9902275519:
100 × (1-.9902275519) = **0.977245%** skill. Against the incumbent its ratio is
.9983573125, or **0.164269%** incremental skill. That is not a 0.98% investment
return. It beats incumbent in 86/135 cells; heads do so in 57/135.

The floor finalist changes both minimum neural scale and blend weight relative
to incumbent. Its incremental result is for that joint configuration, not a
causal estimate of the floor setting alone.

| Model | Reference | Skill % | 60-date nominal 95% interval % | 120-date nominal 95% interval % |
|---|---|---:|---:|---:|
| Incumbent 25% (primary) | Empirical | +0.818035 | [+0.182652, +1.465502] | [+0.263356, +1.507207] |
| Floor .75 / 35% (descriptive) | Empirical | +0.977245 | [+0.024872, +1.878604] | [+0.252448, +1.795899] |
| Floor .75 / 35% (descriptive) | Incumbent | +0.164269 | [-0.237669, +0.530935] | [-0.183884, +0.473858] |
| Heads / 25% (descriptive) | Empirical | +0.793238 | [+0.148202, +1.398437] | [+0.247225, +1.447701] |
| Heads / 25% (descriptive) | Incumbent | -0.026627 | [-0.246574, +0.168640] | [-0.218932, +0.147302] |

Both challengers' incremental intervals include zero. These use 400 paired,
circular date-block resamples of width 60 or 120, seed 829; each resampled date
carries all its indexes/horizons together. The intervals are nominal 95%,
unadjusted for searching nine candidates × two variants (18 choices), the
group/guard selection, multiple metrics, or earlier experiments. No
family-wise error budget or false-discovery guarantee was allocated. The two
block lengths are dependence sensitivities, not proof that dependence is gone.
Reused-history intervals do not establish fresh-data skill.

### Index breakdown and downside calibration

Every index has 45 exact-day cells. Event rates below use each observation
equally; they are not cell-weighted scores. The desired lower/upper event rates
are each 5%, not zero.

| Index | Model | N | Skill vs empirical % | Skill vs incumbent % | Below-5% events / rate | Above-95% events / rate |
|---|---|---:|---:|---:|---:|---:|
| SPY | Incumbent 25% (primary) | 27,608 | +0.966476 | +0.000000 | 1,840 / 6.6647% | 1,463 / 5.2992% |
| SPY | Floor .75 / 35% (descriptive) | 27,608 | +1.156395 | +0.191404 | 1,860 / 6.7372% | 976 / 3.5352% |
| SPY | Heads / 25% (descriptive) | 27,608 | +0.800864 | -0.166704 | 1,822 / 6.5995% | 1,463 / 5.2992% |
| QQQ | Incumbent 25% (primary) | 21,493 | +1.226832 | +0.000000 | 2,108 / 9.8078% | 1,544 / 7.1837% |
| QQQ | Floor .75 / 35% (descriptive) | 21,493 | +1.632544 | +0.416394 | 2,064 / 9.6031% | 1,097 / 5.1040% |
| QQQ | Heads / 25% (descriptive) | 21,493 | +1.207297 | -0.023214 | 2,056 / 9.5659% | 1,596 / 7.4257% |
| IWM | Incumbent 25% (primary) | 18,983 | +0.260796 | +0.000000 | 1,525 / 8.0335% | 1,068 / 5.6261% |
| IWM | Floor .75 / 35% (descriptive) | 18,983 | +0.142795 | -0.114992 | 1,642 / 8.6498% | 918 / 4.8359% |
| IWM | Heads / 25% (descriptive) | 18,983 | +0.371554 | +0.110038 | 1,626 / 8.5656% | 1,020 / 5.3732% |

Aggregate lower-tail events are empirical 5,405/68,084 = 7.9387%, incumbent
5,473/68,084 = 8.0386%, floor 5,566/68,084 = 8.1752%, and heads
5,504/68,084 = 8.0841%. Pure MLP is 6,970/68,084 = 10.2374%.
The floor model has **93 additional downside events** versus incumbent,
despite a slightly better whole-distribution score. Its upper-tail rate falls
to 2,991/68,084 = 4.3931%, from incumbent 4,075/68,084 = 5.9853%.
The improvement is not balanced calibration of both tails.

SPY has 1,745 dates/1,383 expiry series; QQQ 1,745/1,146; IWM 1,742/966.
Those date sets overlap, so their date counts must not be summed as independent
days. Floor helps average SPY/QQQ CRPS but hurts IWM. Heads help IWM but hurt
SPY and QQQ. No index-specific model routing was predeclared or adopted.

### The 30–45 actual-day region

This region contains 15,785 observations and 48 index/day cells: SPY 6,262,
QQQ 4,902, IWM 4,621. Values remain equal-cell CRPS skill percentages.

| Region | Incumbent vs empirical % | Floor vs empirical % | Floor vs incumbent % | Heads vs empirical % | Heads vs incumbent % |
|---|---:|---:|---:|---:|---:|
| All three | +0.480997 | +0.794368 | +0.319993 | +0.562213 | +0.079671 |
| SPY | +0.519684 | +0.809222 | +0.290701 | +0.225682 | -0.295145 |
| QQQ | +0.963042 | +1.575629 | +0.627417 | +1.184756 | +0.219605 |
| IWM | -0.039735 | -0.001746 | +0.041860 | +0.276201 | +0.314553 |

This descriptive region favors wider floors on average, especially QQQ, but
IWM remains approximately flat against empirical. We did not tune a second
regional winner after seeing these numbers.

### Secondary cutoff and fixed-condor diagnostics

Each entry below is the observation-weighted mean error followed by equal-cell
skill against empirical, on the same 68,084 forecasts.

| Diagnostic | Empirical mean | Incumbent mean / skill % | Floor mean / skill % | Heads mean / skill % |
|---|---:|---:|---:|---:|
| Restricted-tail CDF error | 0.343188803 | 0.338463865 / +1.230553 | 0.337616233 / +1.469780 | 0.338597634 / +1.178533 |
| Cutoff probability Brier error | 0.082900174 | 0.081724616 / +1.273109 | 0.081530275 / +1.479558 | 0.081767069 / +1.191118 |
| Normalized fixed-condor loss MSE | 0.080635248 | 0.077802830 / +2.920413 | 0.077188534 / +3.518917 | 0.077936163 / +2.620992 |

Restricted-tail error integrates squared CDF error over normalized cutoffs
[-2.5,-.5] and [.5,2.5]. Brier error is mean squared probability error at the
four fixed diagnostic strikes. The condor geometry uses normalized strikes
[-2,-1,1,2], not optimized or quoted contracts; loss MSE divides by wider-wing
width squared. Floor's 3.518917% loss-MSE skill versus empirical compares with
incumbent 2.920413%, but this is a secondary diagnostic, **not expected return**.
There is no entry credit, bid/ask, fee, slippage, exercise/assignment or financing
model here. No trading or profitability conclusion follows.

### Tangible CDF example

One saved forecast: SPY, entry 2025-11-12, spot $683.38, nominal expiry and
settlement 2025-12-12, 30 actual calendar days. Reference scale a is
.0328924729849264. These are one forecast's probabilities, not observed success
rates or independent estimates from a large cohort.

| Cutoff K | Empirical P(T ≤ K) | Incumbent | Floor finalist | Head finalist |
|---|---:|---:|---:|---:|
| $649.211 | 9.6351% | 9.2666% | 8.4149% | 8.7216% |
| $683.380 | 32.8393% | 36.0680% | 36.9228% | 35.1469% |
| $717.549 | 86.9725% | 86.9599% | 87.6407% | 86.7836% |

For example, incumbent P(T ≤ $649.211) =
F(log(649.211/683.38)/.0328924729849264) = 9.2666%.
Its 5th/50th/95th percentile prices are $638.07/$692.71/$733.48; the floor
finalist's are $639.64/$691.82/$732.46. This particular floor forecast is
narrower in those price quantiles: a minimum neural scale is not a promise that
refitting plus blending widens every final curve.

### Training and validation observation counts

These are forecast rows, not independent returns. The pooled training N is
used by every neural constituent; the index training N is also the empirical
constituent's index subset. Calibration counts describe held-out fitting of
the optional probability map; the selected raw variants do not use that map.
Validation counts for 2016–2018 are development-selection observations; those
for 2019–2025 are the reused later evaluation. No reported predictive score is
a training score.

| Forecast year | Pooled training N | SPY train / calibration / validation | QQQ train / calibration / validation | IWM train / calibration / validation |
|---|---:|---:|---:|---:|
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

The complete panel has 109,355 rows and 68 columns, entries
2008-01-02–2025-12-12, settlements through 2025-12-15, and 892 missing
own-IV entries handled by training-only imputation. The panel ends in 2025;
this run does not supply genuinely new 2026 validation.

### Full exact-day grid

All values below are 2019–2025 raw-variant CRPS skill versus empirical.
N is paired observations for every displayed model in that cell. Positive is
better; negative is worse. Rare 40/41-day cells have only 12–40 observations
and receive equal cell weight, not equal statistical precision. Large-looking
cell differences are descriptive; there are no per-cell uncertainty guarantees.

### SPY: exact-day skill against empirical

| Actual days | N | Incumbent skill % | Floor .75 / 35% skill % | Heads / 25% skill % |
|---:|---:|---:|---:|---:|
| 1 | 1,004 | +0.984 | +1.276 | +1.184 |
| 2 | 831 | +1.182 | +1.435 | +1.203 |
| 3 | 808 | +2.142 | +2.308 | +2.127 |
| 4 | 807 | +2.326 | +2.440 | +2.101 |
| 5 | 842 | +1.583 | +1.732 | +1.528 |
| 6 | 1,006 | +1.187 | +1.017 | +1.066 |
| 7 | 1,320 | +1.311 | +1.461 | +1.323 |
| 8 | 1,019 | +1.037 | +1.203 | +1.019 |
| 9 | 827 | +1.188 | +1.348 | +1.313 |
| 10 | 805 | +1.131 | +1.069 | +1.169 |
| 11 | 805 | +1.435 | +1.403 | +1.407 |
| 12 | 839 | +1.618 | +1.911 | +1.779 |
| 13 | 1,001 | +1.323 | +1.454 | +1.303 |
| 14 | 1,290 | +1.194 | +1.280 | +1.189 |
| 15 | 591 | +1.125 | +1.141 | +0.846 |
| 16 | 547 | +1.238 | +1.503 | +1.034 |
| 17 | 544 | +1.015 | +0.576 | +0.915 |
| 18 | 520 | +0.947 | +0.769 | +0.814 |
| 19 | 410 | +1.325 | +1.239 | +1.311 |
| 20 | 429 | +1.477 | +1.848 | +1.270 |
| 21 | 752 | +0.989 | +1.232 | +0.933 |
| 22 | 582 | +0.868 | +1.071 | +0.626 |
| 23 | 546 | +0.974 | +1.498 | +0.809 |
| 24 | 543 | +0.928 | +0.865 | +0.711 |
| 25 | 518 | +0.925 | +1.110 | +0.611 |
| 26 | 407 | +1.303 | +1.556 | +1.124 |
| 27 | 426 | +1.111 | +1.477 | +0.818 |
| 28 | 748 | +0.713 | +1.136 | +0.586 |
| 29 | 579 | +0.597 | +0.734 | +0.307 |
| 30 | 543 | +0.702 | +1.193 | +0.432 |
| 31 | 539 | +0.822 | +0.721 | +0.772 |
| 32 | 517 | +0.777 | +1.076 | +0.469 |
| 33 | 403 | +1.053 | +1.349 | +0.856 |
| 34 | 427 | +0.759 | +1.037 | +0.581 |
| 35 | 746 | +0.609 | +0.958 | +0.459 |
| 36 | 576 | +0.458 | +0.713 | +0.384 |
| 37 | 368 | +0.394 | +0.600 | +0.070 |
| 38 | 536 | +0.626 | +0.616 | +0.518 |
| 39 | 348 | +0.556 | +1.088 | +0.528 |
| 40 | 23 | -0.394 | +0.279 | -0.973 |
| 41 | 40 | +0.127 | +1.083 | -0.914 |
| 42 | 370 | +0.201 | +0.244 | +0.129 |
| 43 | 364 | +0.347 | +0.246 | +0.011 |
| 44 | 236 | +0.631 | +0.926 | +0.020 |
| 45 | 226 | +0.647 | +0.821 | +0.270 |

### QQQ: exact-day skill against empirical

| Actual days | N | Incumbent skill % | Floor .75 / 35% skill % | Heads / 25% skill % |
|---:|---:|---:|---:|---:|
| 1 | 887 | +0.816 | +0.786 | +0.774 |
| 2 | 724 | +1.134 | +1.043 | +0.984 |
| 3 | 705 | +1.836 | +1.766 | +1.662 |
| 4 | 689 | +1.956 | +2.018 | +1.740 |
| 5 | 613 | +2.917 | +3.709 | +2.673 |
| 6 | 774 | +1.821 | +1.813 | +1.568 |
| 7 | 1,095 | +1.189 | +0.945 | +0.972 |
| 8 | 893 | +0.872 | +1.069 | +0.697 |
| 9 | 720 | +0.945 | +0.862 | +0.956 |
| 10 | 703 | +0.887 | +0.493 | +0.749 |
| 11 | 687 | +1.137 | +1.234 | +1.004 |
| 12 | 610 | +4.797 | +6.171 | +4.252 |
| 13 | 769 | +2.802 | +3.565 | +2.414 |
| 14 | 1,064 | +1.224 | +1.221 | +0.957 |
| 15 | 461 | +0.573 | +0.816 | +0.458 |
| 16 | 438 | +0.697 | +0.943 | +0.464 |
| 17 | 438 | +0.526 | -0.174 | +0.401 |
| 18 | 400 | +0.254 | +0.285 | +0.252 |
| 19 | 174 | +3.928 | +4.483 | +2.928 |
| 20 | 190 | +2.043 | +3.517 | +2.139 |
| 21 | 521 | +0.374 | +0.465 | +0.486 |
| 22 | 452 | +0.286 | +0.468 | +0.209 |
| 23 | 434 | +0.374 | +0.782 | +0.567 |
| 24 | 435 | +0.470 | +0.335 | +0.383 |
| 25 | 397 | +0.053 | +0.410 | +0.155 |
| 26 | 171 | +3.193 | +4.523 | +2.671 |
| 27 | 185 | +2.295 | +3.766 | +2.146 |
| 28 | 511 | +0.165 | +0.525 | +0.463 |
| 29 | 451 | +0.237 | +0.415 | +0.248 |
| 30 | 432 | +0.252 | +0.698 | +0.320 |
| 31 | 431 | +0.425 | +0.142 | +0.706 |
| 32 | 396 | -0.057 | +0.428 | +0.301 |
| 33 | 167 | +6.664 | +8.973 | +5.978 |
| 34 | 185 | +1.808 | +2.860 | +2.112 |
| 35 | 513 | +0.135 | +0.350 | +0.483 |
| 36 | 444 | -0.031 | +0.468 | +0.425 |
| 37 | 362 | +0.571 | +0.957 | +0.550 |
| 38 | 426 | +0.311 | +0.538 | +0.679 |
| 39 | 332 | -0.220 | +0.277 | +0.404 |
| 40 | 16 | +1.260 | +3.082 | +1.783 |
| 41 | 31 | +1.609 | +2.961 | +1.850 |
| 42 | 361 | +0.202 | +0.157 | +0.729 |
| 43 | 355 | +0.155 | +0.555 | +0.558 |
| 44 | 229 | +1.432 | +1.721 | +1.136 |
| 45 | 222 | +0.893 | +1.043 | +0.942 |

### IWM: exact-day skill against empirical

| Actual days | N | Incumbent skill % | Floor .75 / 35% skill % | Heads / 25% skill % |
|---:|---:|---:|---:|---:|
| 1 | 730 | +1.047 | +0.838 | +0.811 |
| 2 | 635 | +0.922 | +0.622 | +0.769 |
| 3 | 625 | +1.219 | +1.400 | +1.000 |
| 4 | 604 | +1.348 | +1.311 | +1.063 |
| 5 | 506 | +1.267 | +1.207 | +1.321 |
| 6 | 598 | +0.738 | +0.861 | +0.622 |
| 7 | 922 | +0.623 | +0.471 | +0.614 |
| 8 | 742 | +0.622 | +0.917 | +0.598 |
| 9 | 631 | +0.775 | +0.917 | +0.844 |
| 10 | 623 | +0.196 | +0.108 | +0.435 |
| 11 | 600 | +0.652 | +0.572 | +0.776 |
| 12 | 501 | +2.153 | +2.137 | +2.166 |
| 13 | 590 | +1.093 | +0.794 | +0.959 |
| 14 | 899 | +0.405 | +0.232 | +0.439 |
| 15 | 429 | +0.399 | +0.669 | +0.339 |
| 16 | 414 | +0.762 | +1.201 | +0.749 |
| 17 | 416 | +0.649 | +0.439 | +0.880 |
| 18 | 376 | +0.746 | +0.607 | +0.644 |
| 19 | 127 | -1.961 | -3.444 | -1.491 |
| 20 | 142 | -0.875 | -2.193 | -1.332 |
| 21 | 475 | +0.373 | +0.375 | +0.431 |
| 22 | 424 | +0.283 | +0.263 | +0.371 |
| 23 | 411 | +0.738 | +0.774 | +0.873 |
| 24 | 413 | +0.769 | +0.305 | +0.842 |
| 25 | 373 | +0.830 | +0.658 | +0.805 |
| 26 | 125 | -1.822 | -2.488 | -1.656 |
| 27 | 140 | -2.116 | -3.599 | -2.361 |
| 28 | 467 | +0.237 | +0.316 | +0.465 |
| 29 | 424 | +0.302 | +0.185 | +0.326 |
| 30 | 409 | +0.491 | +0.724 | +0.576 |
| 31 | 410 | +0.546 | +0.044 | +0.821 |
| 32 | 371 | +0.475 | +0.436 | +0.575 |
| 33 | 121 | -3.004 | -3.827 | -2.341 |
| 34 | 139 | -2.882 | -3.886 | -2.243 |
| 35 | 468 | +0.170 | +0.099 | +0.373 |
| 36 | 419 | +0.220 | +0.448 | +0.513 |
| 37 | 358 | +0.790 | +1.190 | +1.024 |
| 38 | 405 | +0.386 | +0.515 | +0.839 |
| 39 | 326 | +0.727 | +0.830 | +0.984 |
| 40 | 12 | -0.411 | -0.996 | -0.224 |
| 41 | 27 | -0.153 | +1.048 | +0.051 |
| 42 | 359 | +0.522 | +0.697 | +0.871 |
| 43 | 352 | +0.488 | +1.141 | +0.901 |
| 44 | 225 | +0.442 | +1.075 | +0.831 |
| 45 | 220 | +0.558 | +0.434 | +0.868 |


### Numerical audit and recommendation

The standard evaluator audited 120 saved curve files × 12 rows = 1,440
row/curve cases across all four partitions. Against 1,601-node calculations,
the maximum absolute difference in a file's 12-row **mean** CRPS was
.000260766 at 101 nodes and .000022926 at 401; restricted-tail mean differences
were .001787103 and .000389930. These are bounded sampled checks, not
worst-case guarantees over all forecasts or per-row CRPS bounds.
Final headline CRPS uses 401 nodes; tail scoring uses 201 points.
The convergence audit's tail point count changes with its audit resolution.

Maximum price-space versus quantile payoff-integration disagreement on audited
rows was $0.072949/share at 101 draws, $0.026064 at 401, and $0.003138 at
1,601. The fixed price-space diagnostic retains 101 integration points.
These small numerical gaps do not establish realistic option execution.
The 101-node development choice stayed frozen even though the final report
also recomputes development scores at 401 nodes.

**Recommendation: keep the original 75% empirical / 25% MLP blend as the
research benchmark.** Wider floors are a modest lead for a future predeclared
study, not a qualified upgrade: incremental intervals cross zero, IWM weakens,
and downside calibration still misses. Separate heads are not an overall
improvement in this run. Do not relax the one-observation guard after seeing
results, turn descriptive regional results into new routing, or optimize an
iron condor using these tails as if they were validated probabilities.

The next useful work is genuinely new-date validation and a predeclared
downside/cutoff-focused objective, with a justified tolerance decided before
outcomes. Neither is started here. More repeats on the same history may refine
a hypothesis but cannot manufacture an untouched validation set.

## Reproducibility and handoff

Experiment date: 2026-09-28 America/New_York (execution continues in UTC on
September 29). Base: 969fd3e48be10e13313e08a6a14eee47de49664b.
Isolated worktree: /home/russell/dskit-cdf-downside-20260928.
The repository memo workflow separates the execution contract, implementation
checks and empirical outcomes; its plain-language conventions inform this memo.

- JSON: configs/run-predictive-cdf-downside.json
- JSON SHA-256: 2d80052da67c7c3832f659f0a8506f87d489f8c89edeab599e7ec0b505cfa225
- selected.json SHA-256: f55a0c369cb6ba815bc937d2e8033df049e4464996f533f8083c032db7096c4f
- Development scores SHA-256: 8faef53eb1c73ef36f8bab77f8c66002f7ed0b06bd388b71f51a0c0f622b7063
- Output: children/index_options/pipeline_runs/predictive_cdf_downside_20260928
- Logs: children/index_options/pipeline_runs/cdf_downside_logs_20260928
- Evidence and pre-evaluation freeze: docs/review-evidence/ADR-0191.md

The only execution path is the existing standard CLI, from children/index_options:
python -m index_options.cdf_study configs/run-predictive-cdf-downside.json,
with --stage search --partition floor/heads, then --stage select, the documented
parent primary-freeze commit, then --stage evaluate --partition
development/early/middle/late, and finally --stage report. These slash-separated
names denote separate invocations, not literal CLI partition arguments.

Use the prior private interpreter
/home/russell/dskit-cdf-methods-20260928/.venv/bin/python read-only, with the
new checkout and its child first on PYTHONPATH. Every CLI stage runs in WSL2
under systemd MemoryMax=6G, MemorySwapMax=0, CPUQuota=200%, and timeout 1800;
CUBLAS_WORKSPACE_CONFIG=:4096:8, OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1.
At most two stages run concurrently; the first search ran alone. Neural fits
request CUDA; CDF scoring/integration also uses CPU. No one-off fitting/execution
script, shared-environment mutation, live deployment or trading is involved.

### Completed runtime and artifact identities

| Stage | Wall time | Peak RSS KiB | Exit |
|---|---:|---:|---:|
| search floor | 3:12.50 | 2,500,740 | 0 |
| search heads | 2:00.24 | 2,484,292 | 0 |
| select | 0:08.35 | 673,940 | 0 |
| evaluate development | 3:44.11 | 2,460,380 | 0 |
| evaluate early | 5:19.99 | 2,620,256 | 0 |
| evaluate middle | 4:53.85 | 2,574,200 | 0 |
| evaluate late | 4:57.05 | 2,648,548 | 0 |
| report / evaluator | 0:23.53 | 951,968 | 0 |

Maximum per-process RSS was 2.526 GiB; the slowest stage took 5m 19.99s.
Summed stage wall times are 24m 39.62s, with overlapping evaluation stages;
that sum is not elapsed end-to-end time. Each process remained under its
30-minute/6-GiB limit. No other running projects were stopped or modified.

Observed versions: NumPy 2.5.2, pandas 2.3.3, SciPy 1.18.1,
scikit-learn 1.9.0, Torch 2.11.0+cu128, CUDA 12.8. LightGBM 4.7.0 is installed
and recorded but is not a fitted candidate in this iteration. The version
snapshot's global deterministic flags are false outside the per-fit context;
the configured neural fit enables and restores those flags. This is not a
universal GPU bitwise-reproducibility certification. All three controls'
complete raw/calibrated numerical score columns and forecast keys exactly
reproduced the prior refinement report in an independent parent check.

Identity types below are distinct: JSON bytes, canonical experiment identity,
canonical panel identity and result-file hashes are not interchangeable.

- Canonical experiment: 0d6f9657fb5a8eb0c6add6912e6a6fa20762ce35a27528bc0824c65b61bc931b
- Canonical panel: e24b1de89b4af4cb48651f2189930ad826ad96f955b57a8b5e8f142bf26fce2f
- Implementation SHA-256: 6e4e84df05cdb83fe01aed4d739d3881b9db937e3036e3f88384e3e9e7b52932
- report/complete.json: 5e994c8cdd6e526172705b037ff76aa3386bcb7045ef20e43915373c397c835c
- report/comparison.json: 2f6cca116d043f026baee1b5e97400421ca82ef94dc7129b48834999254bf84a
- report/scores.parquet: 67d98b683cd21368853bfc92255314914ed18e900b565a1392a8a494d9b8a13e
- report/convergence.json: ceac058f41be52756aeccb8cdfefd0849cb8e035c7ad99bc17ed2bfa5e5e7de4

The final evaluator produced comparison.json, scores.parquet, skill_by_exact_day.csv,
metrics_by_index.csv, raw_vs_calibrated.csv, convergence.json and complete.json.
Each evaluation directory preserves exact constituent CDFs, calibration maps,
input panel, annual counts, equivalence ledger, versions, protocol and provenance.
The ignored artifacts stay in the isolated worktree; the memo/config/tests and
evidence are the tracked handoff, not a serialized production-serving model.

Journal action A0609 records the completed run via the public journal CLI;
the generated Actions view keeps its latest ten rows while the full ledger
remains append-only. Owner-controlled Path to Production was not changed.

Publication is pending the final documentation/journal commit. Preserve all prior
worktrees, private environments and ignored outputs. No experiment remains running.
