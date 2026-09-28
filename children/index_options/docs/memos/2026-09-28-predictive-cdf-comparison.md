## TL;DR

Keep the horizon-conditioned empirical CDF as the reference model; no learned
model beat it overall in this bounded run. Keep the three-component MLP as a
**SPY research challenger**, not the final architecture: its SPY distribution
skill was +1.41%, versus −0.84% for QQQ and −4.73% for IWM, with incomplete tail
calibration and no demonstrated trading edge. The full corrected comparison
completed in 10 minutes 29.56 seconds with 2.91 GiB peak host RAM.

## What this comparison does

Execution date: September 28, 2026. This is a comparison of distributions of
**the ETF's price at its actual listed expiry**, not a volatility-target rerun,
an options-price model, or a trading backtest. One forecast answers questions
at every strike for one index, entry date and nominal expiry. We compared four
conditional-distribution approaches plus two controls on identical observations.

The outcome is `log(terminal raw close / entry raw close)`. For numerical
stability, each model predicts that return divided by a scale known at entry:
`max(rv_22, 0.001) × sqrt(planned remaining exchange sessions)`. This does not
replace the exact expiry with 22 sessions: 22 is a backward-looking feature
window, while the forecast's remaining horizon varies with the actual contract.

The model-facing schedule includes regular exchange holidays, but excludes
ad-hoc closures, including those announced after entry. Final historical
settlement dates are used only to construct outcomes, purge labels and report
actual-day results. Nominal contract identities remain distinct even when two
expiries share a settlement date. This fixed planned-calendar approximation
also ignores ad-hoc closures already announced at entry; it is causal but not
a full historical announcement-calendar service.

## Models and shared inputs

| Run name | What was fitted | Role / limitation |
|---|---|---|
| `horizon_empirical` | Training terminal-return distribution for the same planned calendar-day horizon; 401 quantile knots | Primary no-feature baseline; interpolated empirical curve |
| `scaled_empirical` | Ridge model for conditional log absolute standardized return; distribution shape from held-out, scaled calibration residuals | Simple conditional-scale benchmark, not the previous RMS-volatility ElasticNet |
| `monotone_cdf` | One LightGBM classifier learns probability below a cutoff; probability constrained to increase with cutoff | Direct CDF, 15 interior cutoffs and fixed standardized endpoints ±8 |
| `quantile_lightgbm` | 15 LightGBM quantile regressors from 0.5% to 99.5%; predictions sorted to remove crossings | Flexible shape; endpoint extension by four standardized units is an explicit finite-tail assumption |
| `mlp_1` | Two independently seeded one-Gaussian MLPs, averaged | Small distribution-network control; final ensemble has **two**, not one, Gaussian terms |
| `mlp_3` | Two independently seeded three-Gaussian MLPs, averaged | Flexible mixture-density network; final ensemble has six Gaussian terms |

Both MLPs use 39 inputs → 32 tanh units → 32 tanh units → distribution
parameters, trained for 20 epochs with negative log likelihood, AdamW, batch
size 1,024, learning rate 0.001, weight decay 0.01 and gradient clipping at 5.
Seed values are 11 and 29. Mixture weights are positive and sum to one;
standard deviations are `softplus(output) + 0.1`. The two-network totals are
4,870 parameters for `mlp_1` and 5,266 for `mlp_3`. Both ran on CUDA; there is
no silent CPU fallback. These are trained nonlinear models, not linear
regressions with a different name.

Every learned model receives the same 39 features:

- 22 backward daily log returns and realized volatility over 1, 5, 22 and
  66 observed sessions;
- its own volatility index: VIX for SPY, VXN for QQQ, RVX for IWM;
- remaining calendar days and sessions, their logarithms, observed series age,
  total observed tenor, elapsed-life fractions and calendar-days-per-session;
- the known reference scale above.

Series age means time since the archive first observed the expiry, not a
verified exchange listing date. Missing values are imputed using training
medians only. Standardization is also training-only. The empirical control
deliberately uses only horizon. Chain-level fields remain in the input panel
for provenance but are **not** among these 39 predictors. This is a bounded,
matched-feature architecture comparison, not another complete feature-selection
or HPO sweep. Fixed training budgets do not establish each architecture's best
attainable performance.

The MLP follows the [mixture-density-network approach](https://www.microsoft.com/en-us/research/publication/mixture-density-networks/).
The direct classifier is inspired by [conditional CDF estimation](https://arxiv.org/abs/1711.05681),
implemented here with monotone boosting, not that paper's exact estimator.
Quantile sorting follows [rearrangement](https://arxiv.org/abs/0704.3649).
Optional held-out probability recalibration follows the principle of
[calibrated regression](https://proceedings.mlr.press/v80/kuleshov18a.html).

## Data and chronological training procedure

The complete admitted panel has 109,355 index/date/nominal-expiry observations,
4,514 distinct dates and 5,089 index/expiry series. Complete raw-close paths
are required. All methods get exactly the same rows; no model-specific sampling.

| Index | Complete panel rows | Development 2016–2018 rows / dates / series | Research evaluation 2019–2025 rows / dates / series |
|---|---:|---:|---:|
| SPY | 44,770 | 9,121 / 753 / 342 | 27,608 / 1,745 / 1,383 |
| QQQ | 32,639 | 4,943 / 752 / 165 | 21,493 / 1,745 / 1,146 |
| IWM | 31,946 | 4,950 / 753 / 165 | 18,983 / 1,742 / 966 |

For each index and forecast year Y, fit on earlier entries whose outcomes were
known **before January 1 of Y−1**; reserve Y−1 entries whose outcomes were known
**before January 1 of Y** for calibration; forecast entries during Y. For
example, 2025 forecasts fit through completed 2023 outcomes, calibrate on
completed 2024 outcomes and evaluate on 2025 entries. Both boundaries purge
unsettled labels. The validation year is not used for early stopping or fitting.
The last year ends where complete archived outcomes remain available, not at
an invented December 31 observation.

This produces 30 index/year folds. Rows overlap in date, expiry and future
return windows, so 68,084 research-evaluation rows are **not** 68,084 independent
market experiments. Earlier evaluation years can enter the training history
of later annual folds once their labels become available; this is intentional
expanding-window retraining. Fit/calibration counts are per fold, not disjoint
sample totals.

Raw and probability-recalibrated curves are both scored. The global raw versus
recalibrated choice for each architecture uses only 2016–2018 development
scores; the primary empirical baseline remains raw. The simple scaled-empirical
method uses calibration data to estimate residual shape instead of adding a
second calibration map. The eventual architecture recommendation uses the
2019–2025 comparison and therefore needs a subsequent untouched evaluation.
Those historical years have also been inspected in previous project work;
they are research evaluation, **not a pristine final test set**.

Admission notes: one SPY holiday quote and 485 incomplete future paths were
excluded; 892 missing IWM volatility-index values are training-median imputed.
All 31,946 admitted IWM observations have unknown dividend metadata. That does
not prevent predicting raw terminal prices, but it **does** prevent treating
these rows as approved condor entries. SPY/QQQ admitted windows have known
dividends. The archive was acquired retrospectively; fingerprints do not prove
point-in-time vendor availability. No historical missing close sessions were
found in a separate XNYS-calendar audit of the three source histories.

## How performance is measured

The primary score is CRPS: a penalty for putting probability in the wrong
places across the whole outcome distribution. Lower is better. Each exact
actual-day/index cell is scored relative to the empirical baseline, then all
135 cells (three indexes × 45 day values) receive equal weight:

`skill % = 100 × [1 − mean_cell(model mean CRPS / baseline mean CRPS)]`.

Positive skill means improvement, negative means worse. This avoids letting
the most common expiries dominate the ranking. Per-row mean scores are also
reported, but do not replace this predeclared primary weighting. Standardized
CRPS is the primary unit; raw log-return CRPS multiplies each observation's
score by its known reference scale.

Other checks cover lower/upper 5% tail frequencies, tail-focused CDF error,
probability error at four fixed cutoffs, and condor-loss prediction error.
Tail integration uses standardized ranges [−2.5, −0.5] and [0.5, 2.5]; this
does not claim to assess arbitrarily extreme crash tails. A calibrated 90%
interval should leave roughly 5% below and 5% above, but pooled coverage can
hide regime and horizon failures.

Uncertainty uses 400 paired circular bootstrap resamples of 60 or 120 entry
dates. Each date carries all its indexes and horizons together, preserving
cross-sectional pairing and substantial overlap. The reported 95% intervals
are pointwise exploratory intervals. There are six architectures and two
references (plus two block-length sensitivity checks); **no family-wise error
budget or multiplicity correction was applied**. Selection is not certified
by a positive point estimate or one interval alone.

## Results

All six selected variants were **raw**. Recalibration worsened development
CRPS for every method that offered a second calibration map. The raw
development score ratios were empirical 1.0000, MLP-1 1.0178, MLP-3 1.0417,
monotone CDF 1.0668, quantile boosting 1.0795 and scaled empirical 1.0802
(lower is better; denominator is the empirical score in each cell).

### Overall research evaluation: 2019–2025

Every row below uses the same **68,084 forecast observations, 1,746 entry
dates, 3,495 index/expiry series and 135 exact-day cells**. Skill columns are
equal-cell relative improvements; CRPS mean is the pooled observation mean.
Intervals are percentage points of CRPS skill versus empirical.

| Model | Mean CRPS | CRPS skill | 95% interval, 60 dates | 95% interval, 120 dates | Condor-loss MSE skill |
|---|---:|---:|---:|---:|---:|
| Horizon empirical | 0.606705 | 0.00% | reference | reference | 0.00% |
| Monotone CDF | 0.607193 | −0.91% | [−3.31, +1.44] | [−3.49, +1.82] | +2.80% |
| MLP-1 | 0.609391 | −1.19% | [−3.31, +1.01] | [−3.19, +1.24] | +0.61% |
| MLP-3 | 0.607278 | −1.39% | [−4.90, +2.03] | [−5.08, +2.87] | +1.12% |
| Quantile LightGBM | 0.616163 | −3.10% | [−6.87, +0.43] | [−7.04, +1.29] | +0.45% |
| Scaled empirical | 0.624370 | −3.33% | [−7.11, −0.45] | [−6.33, −0.36] | −4.63% |

MLP-3's mean cell ratio is 1.01385494, giving
`100 × (1 − 1.01385494) = −1.385494%`: slightly worse, not positive overall
skill. MLP-1 and MLP-3 beat the *weaker scaled-empirical* reference by +2.02%
and +1.79%; their 120-date intervals, [−0.81,+5.43] and [−2.09,+6.47], still
span zero. The result is not evidence that either beats the stronger baseline.

The condor-loss metric favors monotone CDF, not MLP-3. These modest positive
point estimates are secondary diagnostic improvements, without a dedicated
payoff-skill confidence interval or a profitability test. The different CRPS
and payoff rankings are a reason to retain both diagnostics, not to switch the
primary metric after seeing the winner.

### Index-level CRPS skill

Each index averages its 45 actual-day cells. These are descriptive subgroup
results, not multiplicity-adjusted decisions to choose a different model for
every index or expiry.

| Model | SPY: 27,608 rows | QQQ: 21,493 rows | IWM: 18,983 rows |
|---|---:|---:|---:|
| Horizon empirical | 0.00% | 0.00% | 0.00% |
| Monotone CDF | +0.07% | +0.46% | −3.26% |
| MLP-1 | −0.95% | −1.03% | −1.58% |
| MLP-3 | +1.41% | −0.84% | −4.73% |
| Quantile LightGBM | −1.68% | −1.64% | −5.97% |
| Scaled empirical | −4.20% | −4.40% | −1.39% |

MLP-3 improves 41/45 SPY cells, 13/45 QQQ cells and 3/45 IWM cells. These are
dependent cells, not 135 independent significance tests. For the original
**30–45-calendar-day region**, MLP-3 averages only **+0.14% SPY, −0.73% QQQ,
−5.60% IWM**. Thus the attractive SPY-wide result mostly comes from shorter
horizons; it is not strong evidence for the original longer-dated condors.

Some cells are thin: 40-day IWM has 12 observations. Equal-cell weighting makes
that visible instead of silently hiding it behind large cells. A post-hoc
sensitivity check excluding all cells with fewer than 100 rows leaves 129
cells: MLP-3 remains negative overall (−1.34%), and no learned model becomes
positive. That check does not replace the predeclared primary comparison.

### Tail calibration: failures matter for condors

For each model, both rates below should be around 5%. Counts have the same
68,084-row denominator; overlapping observations are not independent.

| Model | Below predicted 5th percentile | Above predicted 95th percentile |
|---|---:|---:|
| Horizon empirical | 5,405 / 68,084 = 7.94% | 6,525 / 68,084 = 9.58% |
| Monotone CDF | 4,100 / 68,084 = 6.02% | 2,427 / 68,084 = 3.56% |
| MLP-1 | 6,572 / 68,084 = 9.65% | 2,715 / 68,084 = 3.99% |
| MLP-3 | 5,406 / 68,084 = 7.94% | 4,516 / 68,084 = 6.63% |
| Quantile LightGBM | 7,171 / 68,084 = 10.53% | 5,475 / 68,084 = 8.04% |
| Scaled empirical | 4,042 / 68,084 = 5.94% | 4,491 / 68,084 = 6.60% |

MLP-3's predicted central 90% band covers only 85.43% of outcomes. The baseline
is also poorly calibrated, covering 82.48%; being the best primary-score
reference does not make it reliable enough for trading. MLP-1's asymmetric
tail failures are especially concerning for short puts. Automatic PIT
recalibration was tested, but its development result did not justify using it.

### Training statistics are not validation statistics

Below are final-year split sizes, shared by every model. Earlier per-fold
counts, date ranges and latest permitted label dates are in `counts.json`.

| 2025 fold | Training rows / dates / series | Calibration rows / dates / series | Evaluation rows / dates / series |
|---|---:|---:|---:|
| SPY | 37,626 / 4,022 / 1,563 | 3,562 / 251 / 251 | 3,324 / 237 / 238 |
| QQQ | 25,595 / 3,208 / 1,075 | 3,462 / 251 / 251 | 3,334 / 238 / 239 |
| IWM | 25,244 / 4,018 / 1,004 | 3,174 / 250 / 223 | 3,291 / 238 / 239 |

Final-epoch **training** negative log likelihood for the two seeds, in
standardized-return density units, was SPY MLP-1 [1.4450,1.4419] versus MLP-3
[1.3298,1.3193]; QQQ [1.3662,1.3546] versus [1.3232,1.3119]; IWM
[1.4727,1.4742] versus [1.3809,1.3904]. These are optimization diagnostics,
not comparable to CRPS and not proof of predictive skill. The larger mixture
fits training data better; its weaker IWM evaluation is consistent with excess
flexibility or distribution change, but this run does not isolate the cause.

### Recommendation

1. **Do not promote an MLP as the final architecture yet.** Keep the empirical
   horizon CDF as the scored reference, not a trading-ready model.
2. **Retain MLP-3 as the SPY research challenger**, alongside monotone CDF for
   payoff-focused comparison and MLP-1 as a complexity control. The smooth,
   directly queryable curve is useful; utility alone is not forecast skill.
3. Next, predeclare a modest development-only tuning/calibration study focused
   on the intended maturity region, compare these contenders with matched
   features, and freeze the rule before new evaluation. Do not run a large
   architecture/strike search on these same years and call the winning result
   an untouched test. More complex networks are not the immediate priority.
4. Only after reliable tail and payoff forecasts on fresh data should we test
   executable listed condors with costs and assignment handling. No trading or
   optimizer has been started by this comparison.

### Full MLP-3 exact-day grid

Each entry is **CRPS skill versus empirical / number of forecast rows** for
2019–2025. Days mean actual calendar days from quote to observed settlement.
The complete six-model, five-score grid is `skill_by_exact_day.csv`: CRPS,
raw-return CRPS, tail CRPS, strike Brier and condor-loss MSE, using the selected
raw variants.

| Days | SPY skill / N | QQQ skill / N | IWM skill / N |
|---:|---:|---:|---:|
| 1 | +2.75% / 1004 | +1.34% / 887 | +1.15% / 730 |
| 2 | +2.36% / 831 | +1.24% / 724 | +0.81% / 635 |
| 3 | +5.06% / 808 | +0.12% / 705 | −0.59% / 625 |
| 4 | +4.73% / 807 | +1.38% / 689 | −0.32% / 604 |
| 5 | +3.97% / 842 | +3.29% / 613 | −2.02% / 506 |
| 6 | +2.60% / 1006 | +2.07% / 774 | −0.92% / 598 |
| 7 | +2.85% / 1320 | −0.46% / 1095 | −1.08% / 922 |
| 8 | +1.86% / 1019 | −0.18% / 893 | −0.86% / 742 |
| 9 | +2.89% / 827 | +0.13% / 720 | +0.15% / 631 |
| 10 | +2.57% / 805 | −1.90% / 703 | −1.87% / 623 |
| 11 | +2.48% / 805 | −1.80% / 687 | −1.02% / 600 |
| 12 | +3.76% / 839 | +5.20% / 610 | −1.47% / 501 |
| 13 | +2.37% / 1001 | +2.31% / 769 | −2.34% / 590 |
| 14 | +2.63% / 1290 | −0.89% / 1064 | −2.21% / 899 |
| 15 | +1.55% / 591 | −1.43% / 461 | −2.96% / 429 |
| 16 | +1.82% / 547 | −1.41% / 438 | −1.70% / 414 |
| 17 | +1.10% / 544 | −2.96% / 438 | −2.06% / 416 |
| 18 | +0.64% / 520 | −3.39% / 400 | −2.47% / 376 |
| 19 | +1.85% / 410 | −2.27% / 174 | −21.35% / 127 |
| 20 | +1.58% / 429 | −0.94% / 190 | −17.39% / 142 |
| 21 | +1.86% / 752 | −3.28% / 521 | −3.52% / 475 |
| 22 | +0.08% / 582 | −2.65% / 452 | −3.14% / 424 |
| 23 | +1.73% / 546 | −1.10% / 434 | −1.43% / 411 |
| 24 | +1.32% / 543 | −2.87% / 435 | −1.94% / 413 |
| 25 | +0.65% / 518 | −3.41% / 397 | −1.57% / 373 |
| 26 | +2.17% / 407 | −4.18% / 171 | −20.98% / 125 |
| 27 | +0.61% / 426 | −1.64% / 185 | −23.24% / 140 |
| 28 | +1.57% / 748 | −3.46% / 511 | −3.83% / 467 |
| 29 | −0.00% / 579 | −2.93% / 451 | −3.05% / 424 |
| 30 | +1.32% / 543 | −1.61% / 432 | −2.25% / 409 |
| 31 | +1.87% / 539 | −2.09% / 431 | −2.16% / 410 |
| 32 | +1.42% / 517 | −3.04% / 396 | −2.26% / 371 |
| 33 | +2.16% / 403 | +1.15% / 167 | −30.60% / 121 |
| 34 | +1.03% / 427 | −2.76% / 185 | −24.33% / 139 |
| 35 | +0.98% / 746 | −3.49% / 513 | −4.96% / 468 |
| 36 | +0.71% / 576 | −2.38% / 444 | −2.83% / 419 |
| 37 | +0.69% / 368 | −0.36% / 362 | −0.48% / 358 |
| 38 | +1.31% / 536 | −1.80% / 426 | −2.79% / 405 |
| 39 | +1.70% / 348 | −2.27% / 332 | −0.28% / 326 |
| 40 | −8.42% / 23 | +4.81% / 16 | −5.50% / 12 |
| 41 | −3.08% / 40 | +4.42% / 31 | −6.84% / 27 |
| 42 | +0.06% / 370 | −1.73% / 361 | −0.38% / 359 |
| 43 | −0.17% / 364 | −1.19% / 355 | −0.42% / 352 |
| 44 | +0.10% / 236 | +1.11% / 229 | −1.24% / 225 |
| 45 | +0.52% / 226 | −0.49% / 222 | −2.33% / 220 |

## From the curve to an iron condor

For entry price S, a possible terminal price K, known return scale a, and
predicted mixture weights w, means m and standard deviations s, the raw MLP
outputs the physical price CDF:

`F(K) = sum_j w_j × NormalCDF((log(K/S)/a − m_j)/s_j)`.

`F(657)` is the model's probability the ETF ends at or below $657, and
`F(708) − F(657)` is its probability of finishing between those strikes.
The model returns an entire function, not just four probabilities. Probability
recalibration, when selected, composes a held-out monotone map with this CDF.

For strikes K1 < K2 < K3 < K4 (long put, short put, short call, long call),
entry credit c dollars per share and a hold-to-expiry European-style payoff:

`expected net P&L/share = c − integral[K1,K2] F(s) ds − integral[K3,K4] (1−F(s)) ds − costs`.

The integrals measure partial losses across the wings; just querying four
endpoints is insufficient for expected return. The conditional **physical**
distribution forecasts actual outcomes. An option-implied, risk-neutral CDF
is different and cannot simply be substituted to claim an excess-return
edge; see the [New York Fed discussion](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr677.pdf).

In the comparison, the four cutoffs are fixed at standardized returns
−2, −1, +1 and +2 for every model. Expected loss is integrated on 101 price
points per wing, checked against 401 predictive quantiles, and compared with
the realized terminal loss. Loss errors are normalized by the wider wing.
These synthetic diagnostic strikes are **not** a listed-strike strategy search.
Entry credit cancels in forecast-versus-realized payoff error; no credits,
fees, fills, American exercise or assignment were backtested.

### Tangible example: SPY, November 12 to December 12, 2025

The saved forecast has entry spot **$683.38**, 30 calendar days, 21 planned
sessions, and reference scale **0.03289247**. Reconstructing the raw MLP-3 CDF
from the saved parameters gives:

| Terminal-price cutoff | MLP-3 probability of finishing at or below it |
|---|---:|
| $635 | 3.93% |
| $657 | 11.25% |
| $708 | 81.71% |
| $720 | 95.28% |

Thus the probability of finishing between $657 and $708 is
`81.7127% − 11.2463% = 70.4663%`. The 5th, 50th and 95th predicted price
percentiles are **$640.00, $692.77 and $719.55**. This is the queryable curve
the user envisaged; the probabilities are model estimates, not guarantees.

The archived 21:00 UTC chain on that date contains the following same-expiry
legs: buy $635 put at ask $1.99, sell $657 put at bid $3.95, sell $708 call at
bid $2.62, buy $720 call at ask $0.95. All four have positive displayed sizes.
The quoted credit is `3.95 + 2.62 − 1.99 − 0.95 = $3.63/share`, or $363 for
one standard 100-share contract per leg. This is an end-of-day quote example,
not evidence of an executable fill after these features became available.

Integrating the same four strikes under each saved raw forecast gives:

| Forecast | Expected terminal option loss/share | Credit minus expected loss, per 100 shares |
|---|---:|---:|
| Horizon empirical | $3.8898 | −$25.98 |
| Monotone CDF | $3.0937 | +$53.63 |
| MLP-1 | $3.7447 | −$11.47 |
| MLP-3 | $2.7821 | +$84.79 |

For MLP-3: `100 × ($3.63 − $2.782137) = $84.7863` before any costs or
assignment effects. The same contracts change from negative to positive
expected value depending on the distribution model: that is **model risk**,
not an opportunity established by this example. The terminal raw close was
$681.76, inside the short strikes; one favorable realized outcome does not
validate these probabilities. No trade was placed. Quotes were rechecked in
the source shard `20260926T011706Z-backfill-838c75fd/option_chain.jsonl.gz`.

## Implementation evidence, failures and limits

The generic CDF estimators and chronological comparison live in dskit's
`predictive_cdf` library pack. The child only constructs exact-expiry index
data and supplies the condor diagnostic. Latest-year artifacts save both raw
and calibrated numerical grids/mixture parameters, calibration maps, row
identities and quantile draws. They permit reconstructing the evaluated CDF;
they are not saved network weights or a released model that can forecast new
dates. A predictive distribution is also **not a confidence band** on that
estimated distribution; parameter/model uncertainty needs additional work.

The first full comparison was deliberately superseded. Independent review
found nominal expiries were lost before pairing and final historical calendars
leaked later-announced closures into maturity predictors. Both were corrected
with regression tests, and the complete run restarted from frozen source
`0fa4da8b`. Earlier numbers in `predictive_cdf_20260928` must not be used for
recommendations. A calibration-endpoint correction also postdated the first
process's module load; the reviewed rerun includes it.

The reviewed comparison is offline research only. It does not authorize
trading, dividend-null entries, an optimizer, online serving, arbitrary-tail
claims, or same-close execution using after-close features. A strategy study
must align decision-time features with executable later quotes, include
spread/fees/funding and American-exercise mechanics, and test selection across
actual available contracts. For the contract payoff itself, see the
[OIC short-condor description](https://www.optionseducation.org/strategies/all-strategies/short-condor).

## Reproducibility and handoff

Run from `children/index_options` in WSL2:

```bash
PYTHONPATH=../.. OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  /usr/bin/time -v timeout 1800 \
  /home/russell/dskit/.venv/bin/python -m index_options.cdf_study \
  configs/run-predictive-cdf-comparison.json
```

The configured output directory is immutable and refuses overwrite. A repeat
needs a fresh output path in a copied config. Data root:
`/home/russell/data/index_options/ob`; cached listed-expiry surface/lifecycle:
`pipeline_runs/exact_maturity_20260928`. Final evidence:
`pipeline_runs/predictive_cdf_reviewed_20260928/` containing input/panel parquet,
row scores, per-fold counts, comparison summary, exact-day skill CSV,
index-level metrics, raw/calibrated metrics, exact 2025 curves, protocol,
dependency versions and source fingerprints. Large artifacts remain local and
ignored; this memo and source/config/review evidence are versioned.

Final execution, review and delivery identities follow below.

- Reviewed source: `0fa4da8b300f25a53f9230cae1152a752cb4103e`.
- Full configuration file SHA-256:
  `ad84da428d4763b123589b4e94833c654c58242db2d0842e77f722e1d5049978`.
  This is a byte-level evidence hash, not a pipeline-node canonical identity.
  A reviewed Minor causes the protocol's intended digest field to be
  overwritten by the forecast-key list; complete config contents survive.
- Corrected command exited 0; all 30 folds completed, generating 1,045,176
  score records (six models × two variants × 87,098 development/evaluation
  observations). All selected evaluation scores are finite and paired.
- Wall time 629.56 seconds; peak resident memory 3,047,744 KiB = 2.91 GiB,
  within the 30-minute/6-GB host-memory budget. GPU memory is separate and was
  not peak-profiled. Average CPU usage was 131% of one core across the run;
  MLP training used the available RTX 5060 Ti CUDA device.
- Versions: NumPy 2.5.2, pandas 2.3.3, SciPy 1.18.1, scikit-learn 1.9.0,
  Torch 2.11.0+cu128, LightGBM 4.7.0, exchange-calendars 4.13.2.
- Two fresh independent final lenses passed the frozen source: correctness
  C0/M0/m1/n0; tests/integration C0/M0/m2/n0. Focused new tests plus pipeline
  import-purity checks: **43 passed**, 15 dependency deprecation warnings.
  No full repository suite was run. Saved curves were independently rebuilt
  from all 36 files and matched recorded probabilities to tolerance 1e−14.
- Deferred Minors: generic backward missing-session bridging (no gaps in this
  corpus), inverse-CDF floating-point behavior at clipped calibration atoms,
  integer-array payoff input dtype (archive prices are float), overwritten
  protocol digest, and incomplete unknown-key rejection inside nested data/
  model-spec objects (the shipped document has no unknown keys). These do not
  establish material error in this run; they remain limits for broader reuse.
- Full retained review reports, frozen source blobs and artifact hashes:
  [`docs/review-evidence/ADR-0189.md`](../../../../docs/review-evidence/ADR-0189.md).
  The implementation and comparison are complete; a future-serving model,
  new-data evaluation and strategy optimization remain deliberately unbuilt.
