# Predictive price CDF: forests, NGBoost and empirical–MLP blends

## TL;DR

The 75% empirical / 25% MLP blend improved full-distribution accuracy by 0.82%
over the empirical baseline; the forest and NGBoost did not improve overall.
Keep the blend as the next research challenger, alongside the empirical
reference: this is reused history, and downside probabilities remain too
optimistic for a trading recommendation.

## Execution contract

The owner requested three further distribution methods, with GPT-5.6 Sol
building and GPT-5.6 Luna reviewing. The target remains the signed raw-price
log return from entry close to actual listed-expiry settlement. It is not
realized volatility. A forecast is a complete cumulative distribution function
(CDF): for a price cutoff K, spot S and causal reference scale a, evaluate
F(log(K/S)/a) to obtain the predicted probability of settling below K.

All training and comparison use the existing generic CDF study and child CLI,
driven by a single JSON document. There is no one-off training script. The
comparison retains the same causal inputs, label purges, row identities,
scoring definitions and empirical reference as ADR-0190. Nothing connects a
broker, selects live contracts, or promotes a model.

The output is historical row-level probabilities, scores and saved curve
states for each evaluation partition's final year. Models are refitted for
each forecast year. This is not a single deployable model checkpoint or a
service that accepts a new option chain; saved numerical curves allow exact
historical cutoff queries and audits, not live-trading authorization.

### What the three methods change

**Quantile-regression forest (QRF).** Trees group observations with similar
inputs; outcomes reaching their leaves inform conditional quantiles. We wrap
the maintained quantile-forest library and represent its predictions as an
explicit, finite-support, 401-knot CDF. This is a mean-split quantile forest,
not the original Fourier-MMD-split Distributional Random Forest. The distinction
matters: mean-based splits can miss changes that affect only spread or tails.
The public grid also cannot invent losses beyond its predicted training-range
endpoints. See the [original QRF paper](https://jmlr.org/papers/v7/meinshausen06a.html)
and [library definition](https://zillow.github.io/quantile-forest/generated/quantile_forest.RandomForestQuantileRegressor.html).
The leaf-sample setting is explicitly unlimited (`max_samples_leaf: null`),
not the library's default of one retained outcome per leaf. Reported numerical
integration convergence assesses this declared curve; it does not certify
that 401 knots exactly reproduce the underlying forest's weighted distribution.

**NGBoost.** Boosted shallow trees predict both the center and spread of a
Normal distribution. We use its CRPS training objective, which scores the
whole distribution rather than only a point prediction. The output is still
a Normal curve: this tests a different learning method, not every possible
tail shape. See [NGBoost's paper](https://proceedings.mlr.press/v119/duan20a.html)
and [official distribution/score interface](https://stanfordmlgroup.github.io/ngboost/1-useage.html).

**Empirical–MLP blend.** For a fixed learned-model weight w, the output is
F_blend(z) = (1−w) F_empirical(z) + w F_MLP(z), with weights 0.10, 0.25 and 0.50
tested on development data. This averages probabilities, not density
parameters, predicted volatility, or quantiles. It is a valid CDF, and its
inverse is computed numerically. The empirical part remains index-specific;
the MLP learns jointly from the three indexes. Weight zero reproduces the
empirical control and weight one reproduces the pooled-MLP control. Training
determinism and fitted-state equality are checked rather than assumed from
matching random seeds. Calibration, if selected, wraps the completed blend.

This is a comparison of complete forecasting methods. Common inputs and rows
do not make the training objectives identical: QRF uses mean-split trees,
NGBoost optimizes CRPS, and the MLP optimizes likelihood.

The predeclared search is deliberately small:

- QRF: 100 trees; minimum leaf sizes 20, 50 or 100; half the input columns
  considered at a split; all leaf outcomes retained; seed 829; two CPU threads.
- NGBoost: 100 boosting rounds; depth/leaf-size/learning-rate combinations
  `(2,20,0.03)`, `(3,20,0.03)`, `(2,50,0.05)`; 80% row subsampling,
  all columns, tolerance 0.0001 and seed 829.
- Blends: MLP weights 10%, 25%, 50%. Every neural constituent and the standalone
  control have one 16-unit tanh hidden layer, one Normal component per seed,
  20 epochs, batches of 1,024, learning rate 0.003, weight decay 0.1,
  minimum scale 0.1, no dropout, and seeds 11 and 29. Their final neural CDF
  averages the two seeds, so it contains two Normal terms, not one.

Every candidate is tested both raw and with a 21-knot probability calibration
map: 18 candidate/variant alternatives, plus the two controls. This is bounded
hyperparameter selection, not a claim of an exhaustive architecture search.

### Training, calibration and model selection

For forecast year Y, model training includes only entries and labels strictly
before January 1 of Y−1. Calibration uses entries in Y−1 whose labels settle
strictly before January 1 of Y. Predictions are scored on entries in Y.
Median imputation and neural scaling are fitted on training rows only. Pooled
models fit once per model/year; calibration remains index-specific. Pooling
weights individual rows equally, not indexes equally.

Development predictions are from 2016–2018, with the additional rule that
every label must settle before 2019-01-01. This prevents late-2018 options from
using 2019 outcomes to choose a model. One candidate and raw/calibrated variant
per method are frozen using development scores before later evaluation.
The pooled MLP control and all blends use the same fixed two-seed ensemble
[11,29]; grouped selection does not change those settings after screening.

Evaluation covers 2019–2025. These years have already informed earlier
research, so they are **not an untouched holdout**, even though each annual
prediction respects chronology. The local option-chain and daily-price archive
cursors both end on 2025-12-15. Repeated comparisons can overfit the research
process without any individual model reading future labels.

### Inputs and counts

All learned methods receive 42 columns: 22 daily return lags; backward realized
volatility over 1/5/22/66 sessions; the index's VIX/VXN/RVX value; planned exact
calendar/session tenor and their logs; observed option-series age and total
tenor; elapsed-life fractions; calendar days per session; reference scale; and
three index indicators. These are unchanged from ADR-0190. Indicator/horizon/
reference values needed to identify a blend's empirical group must be valid
before imputation; missing identity values cannot become invented groups.

Each observation is one index/entry-date/listed-expiry forecast, not one
independent market event. Many observations overlap in time, and the indexes
move together. Reports therefore retain row, date and expiry-series counts;
date-block uncertainty is separate from raw observation counts.

The source panel has 109,355 rows, with entries from 2008-01-02 through
2025-12-12 and settlements through 2025-12-15. Pre-run read-only verification
of the unchanged panel gives 18,616 development rows across 752 dates and
643 index/expiry series: SPY 8,905, QQQ 4,852 and IWM 4,859. Development has
133 observed index/day cells; QQQ/IWM day 26 are absent, not zero-skill cells.
The later evaluation population has 68,084 rows across 1,746 dates and 3,495
series: SPY 27,608, QQQ 21,493 and IWM 18,983, covering all 135 cells.
The completed run verified these same populations with every model/variant
paired. Final reporting contains 867,000 score rows: five models × two variants
× (18,616 development + 68,084 evaluation forecasts).

### How scores are interpreted

The primary score is CRPS: a lower number means the forecast distribution is
closer to the realized outcome. Within each index/exact-day cell, skill is
100 × (1 − model mean CRPS / empirical mean CRPS). The primary overall figure
averages those cell skills equally. It is not the ratio of two row-weighted
overall averages. Positive skill improves on the empirical distribution;
negative skill is worse. Full 1–45-day results remain primary; 30–45-day,
tail and fixed-condor diagnostics are secondary.

The condor diagnostic compares a forecast bounded terminal loss with the
realized loss at fixed synthetic strikes. It does not include received option
premiums, spreads, fees, early assignment, dividends, exits or execution.
Better loss prediction is not proof of positive expected strategy return.
Likewise, a nominal 5th percentile should have about 5% of outcomes below it;
a substantially higher observed rate warns that downside risk is understated.

## Development selection

All nine candidates completed on the same 18,616 development identities and
133 observed cells. Values below are percentage CRPS skill versus the raw
empirical CDF, using the predeclared 101-node screening score. Higher is better.
The raw/calibrated choice and candidate were frozen before running evaluation.

| Candidate | Raw skill % | Calibrated skill % | Frozen? |
|---|---:|---:|---|
| QRF leaf 20 | -6.589 | -19.541 | No |
| QRF leaf 50 | -3.791 | -17.545 | No |
| QRF leaf 100 | -3.318 | -15.756 | Raw |
| NGBoost depth 2, leaf 20, rate .03 | -3.301 | -17.323 | Raw |
| NGBoost depth 3, leaf 20, rate .03 | -4.020 | -18.470 | No |
| NGBoost depth 2, leaf 50, rate .05 | -4.489 | -19.596 | No |
| Blend 10% MLP | +0.577 | -4.192 | No |
| Blend 25% MLP | +1.001 | -4.217 | Raw |
| Blend 50% MLP | +0.522 | -6.055 | No |

The empirical and pooled-MLP controls also froze raw. Control screening skills
were 0% and -4.878%; their calibrated alternatives were -4.548% and -17.879%.
Calibration is not automatically beneficial: the preceding year's correction
can be a poor guide to the next year. These results reject this calibration
procedure here, not probability calibration in general.

Every search partition has 186,160 score rows (five models × two variants ×
18,616 identities), with exact repeated-control equality across partitions.
The blend partition also verified identical fitted neural-state digests for
all three blends and the standalone control in each development year.

## Evaluation results and recommendation

All results below use the frozen raw variants on 2019–2025: 68,084 forecasts,
1,746 entry dates and 3,495 listed-expiry series, across all 135 index/day cells.

| Method | Mean CRPS (row-weighted) | Primary equal-cell skill % | Tail-CRPS skill % | Strike-Brier skill % | Condor-loss MSE skill % |
|---|---:|---:|---:|---:|---:|
| Empirical reference | 0.606705 | 0.000 | 0.000 | 0.000 | 0.000 |
| QRF leaf 100 | 0.619813 | -3.639 | -1.920 | -1.551 | +2.552 |
| NGBoost depth 2 / leaf 20 | 0.606555 | -1.029 | -0.533 | -0.637 | +2.772 |
| 75% empirical / 25% MLP | 0.600837 | +0.818 | +1.231 | +1.273 | +2.920 |
| Standalone pooled MLP | 0.612008 | -1.917 | -0.191 | +0.160 | +3.047 |

CRPS is measured in standardized-return units. The tail score integrates
standardized cutoffs [-2.5,-0.5] and [0.5,2.5], not every possible extreme.
Strike Brier scores squared probability error at standardized cutoffs
[-2,-1,1,2]. Condor-loss MSE is squared loss-prediction error divided by
the wider wing squared; it is dimensionless, not dollar profit.
Raw-return CRPS means were empirical 0.0241471, QRF 0.0246871,
NGBoost 0.0240568, blend 0.0239269 and MLP 0.0248279; corresponding
equal-cell skills were 0%, -2.852%, -0.197%, +0.888% and -3.173%.

For cell c, let M_c and B_c be model and baseline mean CRPS. Primary skill is
100 × mean_c(1 − M_c/B_c), with all 135 cells equally weighted. For the blend,
this is **+0.818035%**. Taking the ratio of the two row-weighted means instead,
100 × (1 − 0.600837267 / 0.606704770), gives **+0.967110%**. That is a different
weighting rule, not the primary result. This also explains why NGBoost's
slightly lower pooled mean does not give it positive equal-cell skill.

| Index | N | QRF skill % | NGBoost skill % | Blend skill % | MLP skill % | Blend positive cells |
|---|---:|---:|---:|---:|---:|---:|
| SPY | 27,608 | -1.791 | +0.273 | +0.966 | -0.565 | 44 / 45 |
| QQQ | 21,493 | -1.680 | +0.541 | +1.227 | -1.536 | 42 / 45 |
| IWM | 18,983 | -7.445 | -3.902 | +0.261 | -3.650 | 37 / 45 |

The blend improves 123/135 cells, but these are correlated forecasts, not
123 independent confirmations. The primary result is modest. For the
secondary 30–45-day region, blend skill is +0.481% overall: SPY +0.520%,
QQQ +0.963%, IWM -0.040%. The same region gives QRF -5.479%, NGBoost -2.579%
and standalone MLP -3.484%. IWM therefore does not show a longer-horizon gain.

### Uncertainty and calibration

Paired circular date-block resampling keeps every index/horizon on a sampled
entry date together. The report uses 400 resamples, seed 829, and 60- and
120-entry-date blocks. Endpoints are the 2.5th and 97.5th percentiles:
nominal 95% pointwise intervals, not simultaneous guarantees.

| Method vs empirical | Skill % | 60-date interval % | 120-date interval % |
|---|---:|---:|---:|
| QRF | -3.639 | [-7.226, -0.272] | [-7.187, +0.383] |
| NGBoost | -1.029 | [-3.500, +1.657] | [-3.568, +2.162] |
| Blend | +0.818 | [+0.183, +1.466] | [+0.263, +1.507] |
| MLP | -1.917 | [-4.584, +0.975] | [-4.464, +1.090] |

The blend also beats the standalone MLP by +2.570% equal-cell skill, with
intervals [+0.490,+4.242]% and [+0.177,+4.293]%. This is encouraging historical
evidence, not a fresh-data guarantee. There is **no multiple-comparison
adjustment or family-wise error budget**: three new finalists × two references
× two block choices produce 12 descriptive intervals, with additional control
comparisons saved. They share data and are dependent. Repeated prior research
on 2019–2025 is a separate source of optimism these intervals cannot correct.

| Method | Outcomes below forecast 5th percentile | Outcomes above forecast 95th percentile |
|---|---:|---:|
| Empirical | 5,405 / 68,084 = 7.939% | 6,525 / 68,084 = 9.584% |
| QRF | 6,086 / 68,084 = 8.939% | 3,500 / 68,084 = 5.141% |
| NGBoost | 7,988 / 68,084 = 11.733% | 2,089 / 68,084 = 3.068% |
| Blend | 5,473 / 68,084 = 8.039% | 4,075 / 68,084 = 5.985% |
| MLP | 6,970 / 68,084 = 10.237% | 2,348 / 68,084 = 3.449% |

Each side should be near 5% for calibrated continuous forecasts. The blend
improves the empirical upper tail but does **not** repair its downside:
5,473/68,084 = 8.039%, about 1.61 times the nominal 5% rate. Its nominal
central 90% interval covers 85.976% of outcomes. A better average score
does not make those strike probabilities trustworthy enough to optimize trades.

Recommendation: retain the empirical reference and carry the frozen 25% blend
forward as the preferred **research challenger**. Do not replace it with this
forest or NGBoost setup, and do not promote the blend to trading. A defensible
next study would predeclare genuinely new-data validation and a downside/
strike-focused objective. No such follow-up, broader HPO, data acquisition or
trading optimization was started here.

### One concrete saved forecast

For SPY entered on 2025-01-08, listed expiry/settlement 2025-02-07 (30 actual
calendar days), spot was $589.49 and reference scale a was 0.04247449.
Querying the saved raw 25% blend gives:

| Expiry-price cutoff K | Forecast probability price ≤ K |
|---:|---:|
| $530.54 | 1.443% |
| $560.02 | 8.053% |
| $589.49 | 32.401% |
| $618.96 | 84.882% |
| $648.44 | 98.327% |

For example, F(log(560.02/589.49)/0.04247449) = 0.080526. These are points on
one price-CDF curve, not five separately trained predictions or five option
recommendations. The example is the first saved 2025 SPY row with 30 actual
days, selected by identity/tenor rather than forecasting success. It illustrates
the interface; the aggregate calibration warning above still applies.

## Full exact-day evaluation grid

Every value is percentage CRPS skill against the raw empirical CDF (0%).
Positive is better. Each model uses the same N forecasts in that cell.
N counts overlapping forecasts, not independent market events. All 135
cells are present; small cells are shown rather than silently excluded.

### SPY

27,608 forecasts; 1,745 entry dates; 1,383 listed-expiry series.

| Actual days | N | QRF % | NGBoost % | Blend % | MLP % |
|---:|---:|---:|---:|---:|---:|
| 1 | 1004 | +1.62 | +1.67 | +0.98 | +0.36 |
| 2 | 831 | +1.40 | +1.59 | +1.18 | +0.79 |
| 3 | 808 | +3.30 | +4.39 | +2.14 | +2.76 |
| 4 | 807 | +2.91 | +4.12 | +2.33 | +4.05 |
| 5 | 842 | +0.94 | +2.50 | +1.58 | +2.34 |
| 6 | 1006 | -0.19 | +1.37 | +1.19 | +0.94 |
| 7 | 1320 | +0.73 | +1.38 | +1.31 | +1.60 |
| 8 | 1019 | +0.56 | +1.26 | +1.04 | +0.30 |
| 9 | 827 | +1.30 | +1.44 | +1.19 | +0.81 |
| 10 | 805 | -0.10 | +1.34 | +1.13 | +0.53 |
| 11 | 805 | -0.24 | +1.88 | +1.43 | +1.48 |
| 12 | 839 | +0.18 | +2.41 | +1.62 | +2.20 |
| 13 | 1001 | -0.11 | +1.76 | +1.32 | +0.89 |
| 14 | 1290 | -0.07 | +1.29 | +1.19 | +0.77 |
| 15 | 591 | -1.64 | +1.22 | +1.12 | +0.09 |
| 16 | 547 | -0.36 | +1.34 | +1.24 | +0.52 |
| 17 | 544 | -2.74 | +0.75 | +1.02 | -0.24 |
| 18 | 520 | -2.76 | +0.15 | +0.95 | -0.66 |
| 19 | 410 | -2.88 | +1.81 | +1.32 | +0.76 |
| 20 | 429 | -2.13 | +2.37 | +1.48 | +1.01 |
| 21 | 752 | -1.28 | +1.03 | +0.99 | -0.42 |
| 22 | 582 | -1.98 | +0.58 | +0.87 | -1.20 |
| 23 | 546 | -0.86 | +0.97 | +0.97 | -0.69 |
| 24 | 543 | -2.93 | +0.39 | +0.93 | -0.60 |
| 25 | 518 | -2.37 | -0.01 | +0.93 | -0.94 |
| 26 | 407 | -2.94 | +1.81 | +1.30 | +0.95 |
| 27 | 426 | -3.30 | +1.15 | +1.11 | +0.13 |
| 28 | 748 | -1.98 | +0.07 | +0.71 | -1.63 |
| 29 | 579 | -2.79 | -0.52 | +0.60 | -2.40 |
| 30 | 543 | -1.77 | -0.39 | +0.70 | -1.88 |
| 31 | 539 | -4.13 | -0.34 | +0.82 | -1.12 |
| 32 | 517 | -3.44 | -0.31 | +0.78 | -1.37 |
| 33 | 403 | -3.69 | +0.60 | +1.05 | -0.27 |
| 34 | 427 | -3.61 | +0.20 | +0.76 | -1.65 |
| 35 | 746 | -2.92 | -0.96 | +0.61 | -2.09 |
| 36 | 576 | -2.40 | -0.89 | +0.46 | -3.02 |
| 37 | 368 | -3.15 | -1.86 | +0.39 | -2.84 |
| 38 | 536 | -4.29 | -1.50 | +0.63 | -1.92 |
| 39 | 348 | -0.56 | -0.97 | +0.56 | -2.48 |
| 40 | 23 | -9.90 | -8.21 | -0.39 | -6.20 |
| 41 | 40 | -4.45 | -4.70 | +0.13 | -3.48 |
| 42 | 370 | -4.59 | -2.35 | +0.20 | -3.69 |
| 43 | 364 | -4.57 | -2.30 | +0.35 | -3.54 |
| 44 | 236 | -2.97 | -2.16 | +0.63 | -2.04 |
| 45 | 226 | -3.47 | -3.11 | +0.65 | -2.35 |

### QQQ

21,493 forecasts; 1,745 entry dates; 1,146 listed-expiry series.

| Actual days | N | QRF % | NGBoost % | Blend % | MLP % |
|---:|---:|---:|---:|---:|---:|
| 1 | 887 | +0.15 | +0.56 | +0.82 | -0.70 |
| 2 | 724 | +0.50 | +0.90 | +1.13 | +0.56 |
| 3 | 705 | +1.71 | +2.48 | +1.84 | +2.20 |
| 4 | 689 | +1.80 | +3.03 | +1.96 | +2.77 |
| 5 | 613 | +4.07 | +5.30 | +2.92 | +4.61 |
| 6 | 774 | +2.19 | +3.65 | +1.82 | +2.94 |
| 7 | 1095 | +0.59 | +1.38 | +1.19 | +0.68 |
| 8 | 893 | +0.69 | +1.60 | +0.87 | -0.77 |
| 9 | 720 | +0.68 | +1.56 | +0.95 | -0.51 |
| 10 | 703 | -0.56 | +1.29 | +0.89 | -0.84 |
| 11 | 687 | -0.42 | +1.67 | +1.14 | -0.23 |
| 12 | 610 | +6.88 | +8.78 | +4.80 | +8.38 |
| 13 | 769 | +3.38 | +5.12 | +2.80 | +3.85 |
| 14 | 1064 | +0.30 | +1.51 | +1.22 | +0.23 |
| 15 | 461 | -0.63 | +0.36 | +0.57 | -2.58 |
| 16 | 438 | -1.10 | +0.51 | +0.70 | -1.88 |
| 17 | 438 | -3.21 | -0.19 | +0.53 | -2.64 |
| 18 | 400 | -2.93 | -0.68 | +0.25 | -3.69 |
| 19 | 174 | -1.60 | +3.58 | +3.93 | +2.36 |
| 20 | 190 | -4.02 | +1.10 | +2.04 | -1.95 |
| 21 | 521 | -2.88 | -0.89 | +0.37 | -3.65 |
| 22 | 452 | -1.72 | -0.63 | +0.29 | -3.90 |
| 23 | 434 | -1.73 | -0.29 | +0.37 | -3.28 |
| 24 | 435 | -3.39 | -0.47 | +0.47 | -2.77 |
| 25 | 397 | -4.00 | -1.66 | +0.05 | -4.85 |
| 26 | 171 | -5.74 | +0.10 | +3.19 | -1.33 |
| 27 | 185 | -4.44 | +0.67 | +2.30 | -3.51 |
| 28 | 511 | -4.09 | -1.86 | +0.16 | -4.53 |
| 29 | 451 | -4.04 | -1.53 | +0.24 | -4.41 |
| 30 | 432 | -3.90 | -1.75 | +0.25 | -3.91 |
| 31 | 431 | -3.82 | -1.13 | +0.43 | -3.34 |
| 32 | 396 | -3.57 | -1.45 | -0.06 | -5.43 |
| 33 | 167 | -1.23 | +4.47 | +6.66 | +1.78 |
| 34 | 185 | -6.43 | -0.82 | +1.81 | -6.27 |
| 35 | 513 | -4.67 | -2.17 | +0.14 | -5.03 |
| 36 | 444 | -4.03 | -1.83 | -0.03 | -5.67 |
| 37 | 362 | -3.54 | -1.25 | +0.57 | -2.39 |
| 38 | 426 | -4.24 | -2.01 | +0.31 | -4.00 |
| 39 | 332 | -1.98 | -2.14 | -0.22 | -5.73 |
| 40 | 16 | +1.55 | +1.95 | +1.26 | +0.55 |
| 41 | 31 | +1.27 | +2.81 | +1.61 | +0.83 |
| 42 | 361 | -4.59 | -2.18 | +0.20 | -4.86 |
| 43 | 355 | -4.26 | -2.13 | +0.15 | -4.97 |
| 44 | 229 | -4.43 | -0.86 | +1.43 | +0.57 |
| 45 | 222 | -4.19 | -2.09 | +0.89 | -1.84 |

### IWM

18,983 forecasts; 1,742 entry dates; 966 listed-expiry series.

| Actual days | N | QRF % | NGBoost % | Blend % | MLP % |
|---:|---:|---:|---:|---:|---:|
| 1 | 730 | -1.48 | -0.50 | +1.05 | +0.10 |
| 2 | 635 | -1.00 | -0.32 | +0.92 | -0.27 |
| 3 | 625 | +0.84 | +1.17 | +1.22 | +0.18 |
| 4 | 604 | -1.02 | +0.44 | +1.35 | +1.18 |
| 5 | 506 | -2.83 | -0.68 | +1.27 | -1.03 |
| 6 | 598 | -2.57 | -0.83 | +0.74 | -0.56 |
| 7 | 922 | -2.23 | -1.39 | +0.62 | -0.98 |
| 8 | 742 | -2.04 | -1.22 | +0.62 | -1.06 |
| 9 | 631 | -1.73 | -0.62 | +0.78 | -0.49 |
| 10 | 623 | -3.02 | -1.14 | +0.20 | -2.89 |
| 11 | 600 | -3.14 | -0.69 | +0.65 | -1.02 |
| 12 | 501 | -2.82 | +0.28 | +2.15 | +0.20 |
| 13 | 590 | -3.09 | -1.56 | +1.09 | -1.06 |
| 14 | 899 | -3.03 | -1.93 | +0.41 | -1.95 |
| 15 | 429 | -5.07 | -2.64 | +0.40 | -2.30 |
| 16 | 414 | -5.02 | -1.85 | +0.76 | -0.73 |
| 17 | 416 | -4.48 | -1.16 | +0.65 | -1.59 |
| 18 | 376 | -4.81 | -1.69 | +0.75 | -0.84 |
| 19 | 127 | -23.21 | -15.73 | -1.96 | -17.43 |
| 20 | 142 | -19.06 | -12.03 | -0.88 | -9.73 |
| 21 | 475 | -5.60 | -2.77 | +0.37 | -2.38 |
| 22 | 424 | -6.35 | -2.76 | +0.28 | -3.02 |
| 23 | 411 | -5.56 | -1.59 | +0.74 | -0.81 |
| 24 | 413 | -6.00 | -1.09 | +0.77 | -1.07 |
| 25 | 373 | -5.55 | -1.67 | +0.83 | -0.60 |
| 26 | 125 | -23.20 | -15.56 | -1.82 | -15.24 |
| 27 | 140 | -24.69 | -17.40 | -2.12 | -15.62 |
| 28 | 467 | -8.04 | -3.50 | +0.24 | -2.93 |
| 29 | 424 | -8.15 | -3.18 | +0.30 | -2.70 |
| 30 | 409 | -7.98 | -3.12 | +0.49 | -1.92 |
| 31 | 410 | -7.58 | -2.21 | +0.55 | -1.96 |
| 32 | 371 | -7.18 | -2.86 | +0.47 | -2.32 |
| 33 | 121 | -32.61 | -24.28 | -3.00 | -25.88 |
| 34 | 139 | -26.02 | -18.80 | -2.88 | -19.64 |
| 35 | 468 | -9.96 | -4.26 | +0.17 | -3.46 |
| 36 | 419 | -8.89 | -3.29 | +0.22 | -3.21 |
| 37 | 358 | -6.29 | -1.32 | +0.79 | -0.67 |
| 38 | 405 | -7.67 | -2.49 | +0.39 | -2.83 |
| 39 | 326 | -4.29 | -1.32 | +0.73 | -1.15 |
| 40 | 12 | -5.66 | -6.19 | -0.41 | -3.47 |
| 41 | 27 | -2.72 | -3.77 | -0.15 | -3.26 |
| 42 | 359 | -5.63 | -1.79 | +0.52 | -1.86 |
| 43 | 352 | -6.59 | -2.08 | +0.49 | -2.08 |
| 44 | 225 | -5.97 | -1.82 | +0.44 | -2.10 |
| 45 | 220 | -6.00 | -2.37 | +0.56 | -1.79 |

## Annual training, calibration and validation counts

The four learned methods use the pooled training N; the empirical control
uses its index's training N. Each index triplet below is **train / calibration
/ validation** rows. Training ends before January 1 of the preceding year;
calibration is that preceding year's strictly settled labels; validation is
the displayed entry year. Thus validation outcomes are not training statistics.
No training CRPS is claimed. Neural training likelihood is a different objective.
The pooled models fit once per year, not once per index.

| Forecast year | Pooled training N | SPY train / cal / val | QQQ train / cal / val | IWM train / cal / val |
|---:|---:|---:|---:|---:|---:|
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

2016–2018 validation additionally excludes labels settling in 2019 or later.
Calibration rows are never used to fit model parameters or training preprocessing.
Raw variants were selected, but calibrated alternatives remain in the evidence.
Exact per-index date counts, expiry-series counts and latest training/calibration
label dates are retained in each partition's counts.json.


## Implementation and review evidence

ADR-0191 and `docs/review-evidence/ADR-0191.md` retain the scope, focused tests,
Luna findings, dispositions and immutable candidate
`1302738e3a38d9ae635ae5b3b2ae2a25028ee122`. The design review
required dependency versions in cross-stage identity and deterministic,
verified blend/control equivalence before implementation. Sol built the changes;
two fresh Luna final lenses each reported zero unresolved Critical/Major.
The current candidate passed 93 focused/purity tests and 28 child integration
tests; no full repository suite was run. Earlier review cycles caught and fixed
year-scoping, forecast-identity audit and pre-filter date-validation defects.

One new Minor is explicitly deferred: the blend inverse can land about 1e-19
below an atom, violating a strict inverse/CDF boundary equality without a
material demonstrated strike/payoff effect. Earlier documented numerical and
configuration-validation findings retain their own recorded dispositions;
this bounded review does not claim a comprehensive production certification.

Luna separately audited completed artifacts: all four evaluation inventories
(37 files each) and the report inventory (six files) matched their hashes;
all model/variant identities paired, all 30 annual fold boundaries/purges held,
and all ten annual blend/control equivalence records verified. Reconstruction
of 24 representative raw/calibrated forest, NGBoost and blend curves across
the four partitions reproduced saved PIT probabilities exactly; quantile
comparison with stored float32 draws differed by at most 8.15e-7.

The report's numerical audit covers 120 curve files × 12 rows = 1,440
row/curve checks at 101/401/1,601 nodes. Among selected raw curves, the largest
absolute change in a file's sampled mean CRPS from 401 to 1,601 nodes was
0.00001805; for tail CRPS, 0.00038137. Maximum sampled CDF-integral versus
quantile-payoff discrepancy was $0.01057/share at 401 nodes and $0.00314/share
at 1,601; across calibrated alternatives too, the 401-node maximum was
$0.01422/share. Price-space integration remains fixed at 101 points. These
are sampled numerical diagnostics, not an exhaustive error bound, a test of
the QRF's 401-knot representation against its full weighted forest, or proof
that a proposed trade's expected value exceeds numerical/execution error.

## Reproducibility and handoff status

Worktree: `/home/russell/dskit-cdf-methods-20260928`. All calculations run in
WSL2. Optional NGBoost 0.5.11 and quantile-forest 1.4.2 dependencies are installed
in its private ignored `.venv`; existing numerical packages are inherited
read-only, without changing the shared environment. Generated research
artifacts remain ignored by Git. Each execution stage used a WSL
user-systemd scope with a hard 6-GiB
memory cap and no additional swap, two CPU cores, and an external 1,800-second
timeout. Two existing small synthetic fit tests passed under these caps in
5.53 seconds with 1,003,344 KiB peak host RSS. This preflight does not establish
full-panel runtime or memory; actual measurements follow below.
MLP training uses CUDA on the RTX 5060 Ti. These QRF and NGBoost libraries use
CPU; that is their selected implementation, not a WSL CUDA failure. Neural
training requires deterministic CUDA prerequisites and refuses silent fallback.
The global determinism flags in versions.json are false because that file is
written before fitting. Each deterministic neural fit enables the required
flags inside its scoped context and restores the prior settings afterward;
the recorded per-year fitted-state matches provide the actual equivalence
evidence. This is not a claim of bitwise portability across different hardware
or dependency versions.

The standardized command, from the child directory, is:

```bash
systemd-run --user --scope --quiet \
  -p MemoryMax=6G -p MemorySwapMax=0 -p CPUQuota=200% \
  env PYTHONPATH=/home/russell/dskit-cdf-methods-20260928:/home/russell/dskit-cdf-methods-20260928/children/index_options \
  CUBLAS_WORKSPACE_CONFIG=:4096:8 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  /usr/bin/time -v timeout 1800 \
  /home/russell/dskit-cdf-methods-20260928/.venv/bin/python \
  -m index_options.cdf_study configs/run-predictive-cdf-methods.json \
  --stage search --partition forest
```

The other declared search partitions are `ngboost` and `blend`; selection uses
`--stage select` without a partition; evaluation uses `--stage evaluate` with
`development`, `early`, `middle`, `late`; the final evaluator is `--stage
report` without a partition. Every invocation uses the same resource wrapper.
At most two stages run concurrently. Logs live in the child's ignored
`pipeline_runs/cdf_methods_logs_20260928`; artifacts live in
`pipeline_runs/predictive_cdf_methods_20260928`. Completed directories refuse
overwrite; partial output is never treated as a completed stage.

### Completed stages and resource measurements

All nine standardized invocations exited 0. No real-data stage was skipped,
timed out, failed, resumed from partial output, or narrowed after seeing scores.
Peak host RSS is a process measurement; the 6-GiB scope also enforces its own
memory accounting. GPU memory is separate from this host-memory cap.

| Stage | Wall time | Peak host RSS, KiB |
|---|---:|---:|
| Search forest | 1:35.31 | 3,062,620 |
| Search NGBoost | 2:48.99 | 2,405,884 |
| Search blend | 1:38.85 | 2,425,912 |
| Select | 0:08.68 | 638,456 |
| Evaluate development, 2016–2018 | 2:44.33 | 2,747,824 |
| Evaluate early, 2019–2021 | 4:29.66 | 3,006,188 |
| Evaluate middle, 2022–2023 | 4:23.20 | 3,126,204 |
| Evaluate late, 2024–2025 | 5:10.85 | 3,407,592 |
| Final report/evaluator | 0:17.95 | 961,932 |

The slowest stage was 310.85 seconds < 1,800; the highest process RSS was
3,407,592 / 1,048,576 = 3.250 GiB < 6. Stage times overlap where two ran
concurrently, so summing them is not elapsed end-to-end wall time.

Runtime versions: Python 3.12.3; NumPy 2.5.2; pandas 2.3.3; SciPy 1.18.1;
scikit-learn 1.9.0; Torch 2.11.0+cu128/CUDA 12.8; LightGBM 4.7.0;
quantile-forest 1.4.2; NGBoost 0.5.11. Configured optional-library versions
are bound into cross-stage identity. The private environment's pip check passed.

### Reproduction identities

Run source: `1302738e3a38d9ae635ae5b3b2ae2a25028ee122`.
These SHA-256 values distinguish the original JSON bytes, canonical execution
identity, numerical data and frozen selection:

| Item | SHA-256 |
|---|---|
| JSON file bytes | da174820172b462df1d309a94c851e08059cda64074dc654d573144566624f98 |
| Canonical config | 85897c46f96f04b0de4f1307686d92c14da48894be7a07c2d749e0e5546762f1 |
| Canonical full panel | e24b1de89b4af4cb48651f2189930ad826ad96f955b57a8b5e8f142bf26fce2f |
| Upstream provenance | 9a399830e9fc81c009ba1353208f06606c89234d31354e8b22f87f17257a5154 |
| Candidate inventory | 50db07833844aa50e737b7642d00f60b915ece15c29e7c16b86c3678dec4c76a |
| Generic implementation | 6e4e84df05cdb83fe01aed4d739d3881b9db937e3036e3f88384e3e9e7b52932 |
| selection/selected.json | bcaea4b4f6f249bbf59d0ab032db0708e1d82893aa1e6654eaada1b920140da4 |
| report/comparison.json | 6a5d3e12338b26b737cf073c3f30d02acf733b7d3873eea5082f9b33b30dd67e |
| report/scores.parquet | 57e63a37aee23e34991d2bc2245be8b842d37a9b9c587eb4452ff6afa5926794 |
| report/convergence.json | d707c64c5c52257a46153e76f5bf394600e90a82801ad56dbca262d8d59e905c |
| report/complete.json | 55733d5d1940ffe11be9ddd75ac250a34cb7d13f5c59221742f7133025c18505 |

The complete report and review gate are finished and published on remote main:
source `1302738e`, results/evidence `df3cec80bd6ec373de724d04116b632db9d31d6e`,
with remote containment verified after fetch. WSL's credential-helper bridge
failed for push; existing Windows Git credentials published the same worktree,
without running experiments on Windows. Preserve this worktree's ignored outputs/private
environment and the older feature-research worktree containing upstream cache
files. No live model, broker, optimal-contract selector, untouched-data test,
full-suite run or broader model search was performed.
