# Tail-constrained option/incumbent quantile blend

**Date:** 2026-09-30
**Decision:** retain the incumbent for now; the blend is a strong challenger but
misses one preregistered development tail guard. This is offline reused-history
research, not a trading or deployment result.

## Result in one sentence

The frozen fine-tail blend improved full-history equal-cell CRPS by **1.307%**
against the incumbent (60-date interval 0.517% to 2.328%; 120-date interval
0.488% to 2.327%) and improved full-history tail deviations for every index,
but its development SPY lower-tail deviation was 0.00828 versus the incumbent's
0.00738, so ADR-0194's strict promotion rule says retain the incumbent.

## What was built

`TailConstrainedQuantileBlendCDF` combines two frozen endpoint distributions:

- incumbent: 75% exact-horizon/index empirical CDF plus 25% one-component MLP;
- challenger: the ADR-0193 option-implied quantile surface transported by a
  training-only PIT map, with empirical fallback when the option proxy is
  ineligible.

For SPY, QQQ and IWM separately, the model chooses option weights at probability
anchors 0.05, 0.50 and 0.95. Weights interpolate linearly between anchors and
remain constant outside them. The blended quantiles are monotonically rearranged.
Actual calendar DTE is used only to equal-weight calibration cells; it is refused
as an endpoint predictor. Zero-hot, multi-hot and nonbinary index identities also
refuse.

The selected grid allowed tail weights
`[0,.05,.10,.15,.20,.25]` and center weights `[0,.25,.50,.75,1]`.
The all-zero candidate was mandatory. A candidate was feasible only when its
calibration-fold below-5% and above-95% deterministic right-CDF PIT deviations
were no worse than the incumbent within `1e-12`.

## Training and validation protocol

The single document
`configs/run-predictive-cdf-tail-blend.json` drove prepare, grouped search,
development selection, four evaluation partitions and the final evaluator.
Every annual fold fit both endpoints on data whose outcomes ended before the
preceding calendar year. The preceding year alone selected the three per-index
weights. The next calendar year was validation. No validation label altered a
fit or weight.

Development was 2016–2018: 18,616 forecasts across 133 exact
index/actual-calendar-DTE cells. Three grids were compared once. The fine-tail
grid was frozen on development CRPS before evaluating 2019–2025. The final
paired panel contains 68,084 forecasts, 1,746 entry dates and 3,495 expiry
series:

| Year | Forecasts | Entry dates | Expiry series |
|---:|---:|---:|---:|
| 2016 | 5,510 | 252 | 83 |
| 2017 | 6,171 | 251 | 115 |
| 2018 | 6,935 | 249 | 149 |
| 2019 | 7,592 | 251 | 172 |
| 2020 | 7,750 | 252 | 176 |
| 2021 | 10,136 | 252 | 173 |
| 2022 | 12,480 | 251 | 182 |
| 2023 | 9,592 | 250 | 263 |
| 2024 | 10,585 | 252 | 264 |
| 2025 | 9,949 | 238 | 239 |

## Development selection

Relative CRPS versus the empirical reference was 0.97712 for the broad grid,
0.97316 for fine tails, and 0.97802 for the very-conservative grid. The frozen
fine-tail model's equal-cell CRPS was 0.59240 versus 0.60315 for the incumbent,
a 1.781% improvement.

The later promotion check nevertheless failed one development condition. SPY's
lower-tail event rate was 0.05828 for the blend and 0.05738 for the incumbent;
their absolute deviations from 0.05 were 0.00828 and 0.00738. The other five
index/tail comparisons improved. The very-conservative grid happened to pass all
six development tail comparisons and improved CRPS by about 1.12%, but it was not
the frozen CRPS winner and was therefore not substituted after inspection.

## Full paired evaluation

| Index | Blend CRPS | Incumbent CRPS | Skill vs incumbent | Blend lower/upper deviation | Incumbent lower/upper deviation |
|---|---:|---:|---:|---:|---:|
| SPY | 0.586918 | 0.599848 | 2.156% | 0.01364 / 0.00074 | 0.01632 / 0.00104 |
| QQQ | 0.582202 | 0.591554 | 1.581% | 0.04747 / 0.01486 | 0.04896 / 0.02295 |
| IWM | 0.608318 | 0.611385 | 0.502% | 0.02923 / 0.00410 | 0.02965 / 0.00795 |

Across all cells, equal-cell skill was 2.215% versus the empirical reference and
1.307% versus the incumbent. The option transport remained better on average at
1.853% versus the incumbent, but the blend deliberately surrendered some average
gain to improve tail behavior. Blend tail CRPS skill was 2.436% versus empirical,
and strike-Brier skill was 2.259%.

## Selected weights by forecast year

Each entry is `(left, center, right)` in SPY / QQQ / IWM order.

| Year | SPY | QQQ | IWM |
|---:|---:|---:|---:|
| 2016 | (0,.00,.25) | (.10,.50,.10) | (0,1,.25) |
| 2017 | (.25,1,.25) | (0,1,.25) | (0,1,0) |
| 2018 | (.25,1,.25) | (.25,.25,.25) | (.25,1,.25) |
| 2019 | (.10,1,.25) | (.25,0,.05) | (0,1,.25) |
| 2020 | (0,1,0) | (.15,1,.20) | (.25,.75,.05) |
| 2021 | (.25,1,.25) | (.25,1,.25) | (0,1,.25) |
| 2022 | (.25,1,.25) | (0,1,.25) | (0,.25,.25) |
| 2023 | (.25,1,.25) | (.25,1,.15) | (.25,0,0) |
| 2024 | (0,1,.25) | (.25,1,.25) | (0,0,.25) |
| 2025 | (.25,1,.25) | (.25,1,.25) | (0,1,0) |

Center weights often reach one while tail weights remain at or below .25. That
is the intended mechanism: use much of the option surface around the body while
keeping empirical/MLP support in the tails. Variation across years also shows
that the calibration selector is active rather than collapsing to one fixed mix.

## Verification and limits

- 124 affected CDF/child tests passed; six directly cover the new estimator.
- Prepare: 109,850 raw-chain rows, 15:58, 1.63 GiB peak RSS.
- Search: 2:39, 2.89 GiB peak RSS. All evaluation stages were under 2:16 and
  3.82 GiB; every stage stayed below 30 minutes and 6 GiB.
- Final Luna review before the endpoint integration correction was C0/M0/m1/n0.
  The minor is interpretive: the guard uses the same deterministic right-CDF PIT
  convention as the evaluator, not randomized PIT for atoms.
- Historical 2019–2025 results were already inspected and are descriptive, not
  a fresh holdout. Intervals are pointwise and not multiplicity-adjusted.
- This predicts a terminal physical return distribution. It does not validate
  transaction costs, execution, sizing, or a profitable iron-condor strategy.

## Recommendation

Keep `incumbent_blend_025` as the governed incumbent. Retain the fine-tail blend
as a promising research challenger. If another iteration is authorized, predeclare
a guard-aware top-level selector (choose the lowest development CRPS only among
grids passing all six development tail guards) and rerun prospectively; do not
retroactively promote the observed very-conservative grid.
