# Dynamic tail calibration and semiparametric GPD tails

**Date:** 2026-09-30  
**Decision:** promote `center_gpd_05_prior25` as the governed offline research
CDF because it passes the preregistered development rule and improves reused
later-history CRPS and proper tail scores. The gain over the prior incumbent is
small and 5% tail coverage is still materially imperfect, especially for QQQ.
This is not yet an industry-acceptable production calibration, contract
optimizer, or trading model.

## What the model outputs

For every SPY, QQQ, or IWM entry date and exact listed expiry, the model emits a
monotone 401-knot quantile curve for standardized terminal return. Multiplying
by the entry-date reference scale recovers a terminal-return distribution;
entry spot then maps it to a terminal-price CDF. A downstream evaluator can
query `P(S_T <= K)` at any strike or integrate bounded option payoffs. The model
does not choose strikes, model fills, size positions, or authorize trading.

The center is the ADR-0196 conditioned-transport incumbent. It conservatively
blends an empirical/MLP physical forecast with an option-implied transport
curve, using only the preceding calibration band for blend weights. The new
outer layer preserves this center from the 5th through 95th percentiles. Below
and above those splice points it fits generalized-Pareto excess curves from
training labels only. It estimates global left/right shape and scale, estimates
the same parameters per index when at least 50 exceedances exist, then shrinks
each index parameter toward global by `n/(n+25)`. Shape is bounded to
`[-0.4, 0.8]`; the probability floor is 0.0001. `anchor_batch_size=4096` only
bounds memory and is numerically identical to the unbatched calculation.

This bulk-plus-GPD construction follows the peaks-over-threshold rationale, but
threshold uncertainty remains genuine: fixed 90%/95% sample thresholds can
trade model approximation against estimation error.[^gpd] Beta-transformed
linear pooling is also a recognized CDF aggregation/calibration family,[^beta]
but did not help this dataset.

## Protocol

The immutable ADR-0196 panel and provenance are reused. Features are entry-known
lagged returns; realized/upside/downside volatility; requested horizon and
series-life seasonality; index identity; option-chain level, skew, curvature,
liquidity, open interest, depth and fixed nodes; volatility indexes and term
structure; VVIX/SKEW/GVZ; and lagged rates, credit, dollar, oil and financial
conditions. `actual_calendar_dte` is reporting-cell metadata and is excluded
from endpoint predictors.

Each forecast year uses an expanding training history, a preceding calibration
band, and a purged validation year; terminal labels crossing a boundary are not
admitted. Search and model choice use 2016–2018 only. Frozen evaluations are
development (2016–2018), early (2019–2021), middle (2022–2023), and late
(2024–2025). Later scores pair all models on the same 68,084 identities: 1,746
entry dates and 3,495 expiry series. The main rank weights every index/exact-DTE
cell equally.

Promotion is now enforced in code, not merely checked after selection. Relative
to ADR-0196, a candidate must have strictly better development equal-cell CRPS,
no worse equal-cell lower and upper integrated quantile scores, and no worse
per-index absolute 5%/95% hit-rate deviation (tolerance `1e-12`). The selected
model passed all nine comparisons before any 2019–2025 result was opened.

## Sequential evidence

Phase A compared dynamic causal PIT maps, finite beta-transformed pools, and
GPD tails around the raw option endpoint. Dynamic PIT and beta pooling worsened
development CRPS/tails. Raw-option GPD improved development CRPS by about 1.18%
but failed the per-index 5%/95% guards, so no later partition was opened.

Phase B preregistered GPD tails around the stronger center incumbent. The 5%
splice with prior strengths 25 and 100 passed; the selector chose prior 25.
Phase B/C/D evaluations exceeded the 6 GiB cap and are invalid. Investigation
found redundant controls were not the cause. Generic CUDA cleanup and bounded
GPD anchor prediction reduced the final late replay peak from 6.14 GiB to 3.63
GiB without changing model numerics.

An independent Luna review then found three substantive implementation gaps:
the selector did not itself enforce tail-score noninferiority, the dynamic PIT
global prior could contain immature calibration outcomes in generic use, and
final CUDA batch tensors were live at cleanup. All were fixed with failing-first
tests. Completion records now retain peak RSS and stage wall time. Phase F is
the only valid final evidence root.

## Results

Development has 18,616 forecasts and 133 exact index/DTE cells. The candidate
improves equal-cell CRPS **0.196%**, lower-tail quantile score **2.912%**, and
upper-tail quantile score **1.517%** versus ADR-0196. Its per-index 5%/95% hit
rates equal ADR-0196 exactly because those splice quantiles are preserved.

On reused 2019–2025 history, equal-cell CRPS improves **0.08045%** versus
ADR-0196. Paired block intervals exclude zero but describe a very small effect:
60-entry-date **0.02795% to 0.12445%** and 120-date **0.02693% to 0.12086%**.
Versus the empirical baseline, equal-cell CRPS skill is **1.8263%** with
60/120-date intervals of **1.0044% to 2.8307%** and **1.0421% to 2.7917%**.

| Index | N | GPD CRPS | ADR-0196 CRPS | Direct skill |
|---|---:|---:|---:|---:|
| SPY | 27,608 | 0.591539 | 0.592046 | +0.0856% |
| QQQ | 21,493 | 0.585638 | 0.586195 | +0.0950% |
| IWM | 18,983 | 0.608750 | 0.608790 | +0.0065% |

| Partition | N/model | CRPS skill vs ADR-0196 | Lower-tail score skill | Upper-tail score skill |
|---|---:|---:|---:|---:|
| Development | 18,616 | +0.1957% | +2.9117% | +1.5165% |
| Early | 25,478 | +0.0693% | +1.0112% | +0.3984% |
| Middle | 22,072 | +0.0980% | +2.1547% | +0.2743% |
| Late | 20,534 | +0.0658% | +0.3544% | +1.6152% |
| Later total | 68,084 | +0.08045% | +1.2684% | +0.6188% |

Later row-weighted lower/upper tail quantile scores improve from
0.286494/0.179940 to **0.283807/0.178694**. Strike Brier improves from 0.081160
to **0.081100**. Condor-loss MSE is effectively tied and slightly worse in raw
row weighting: 0.076775 versus 0.076746; equal-cell later skill is only +0.008%.
This is distributional evidence, not demonstrated strategy alpha.

## Calibration diagnosis

The GPD layer materially changes severity and the deeper quantiles, not the 5%
anchors. Later aggregate hit rates are:

| Tail probability | Target | GPD | ADR-0196 |
|---|---:|---:|---:|
| Below 1% | 1.0% | 1.341% | 1.989% |
| Below 2.5% | 2.5% | 3.832% | 4.311% |
| Below 5% | 5.0% | 7.956% | 7.956% |
| Above 95% | 5.0% | 5.762% | 5.762% |
| Above 97.5% | 2.5% | 3.054% | 2.260% |
| Above 99% | 1.0% | 1.225% | 0.610% |

The proper tail scores improve, and 1% calibration is closer on both sides, but
calibration is not uniformly better at every nominal level. QQQ is the largest
concern: below-5% outcomes occur 9.794% of the time and above-5% outcomes 6.909%.
SPY upper-5% is close at 5.042%; IWM upper-5% is 5.510%. Because the strike
optimizer depends directly on tail probabilities, calling this production-good
would be premature. Industry-acceptable use should additionally require
predeclared per-index/expiry reliability bands, conditional calibration tests,
tail effective-sample and parameter-uncertainty reporting, new-date validation,
and decision backtests including bid/ask, fees and exercise mechanics. Proper
scores and conditional reliability are complementary; marginal PIT uniformity
alone does not establish conditional calibration.[^conditional]

## Verification and recommendation

- Phase F search, selection, all four evaluations, and report completed under
  30 minutes and 6 GiB. External peaks: search 2.81 GiB, development 2.79 GiB,
  early 3.16 GiB, middle 3.42 GiB, late 3.63 GiB, report 1.40 GiB. Slowest was
  late at 4:03. Completion records retain in-process telemetry and file hashes.
- 109 focused core tests pass, including delayed/same-date/future PIT admission,
  immature calibration exclusion, finite beta grids, exact batched GPD anchors,
  GPD pooling/joins, proper tail scores, and enforced promotion guards.
- Later history has been repeatedly inspected across ADR-0193–0197. Intervals
  are pointwise, dependent, and not multiplicity-adjusted fresh-holdout claims.

Promote this model only as the offline research incumbent and CDF input for a
separately governed optimizer prototype. Before production or live capital,
the next highest-value step is forward/new-date validation with stricter
conditional tail calibration. A hierarchical or Bayesian GPD that carries
threshold and parameter uncertainty is the most defensible modeling extension;
more tuning on the same 2019–2025 history is lower-value.

[^beta]: Gneiting and Ranjan, [“Combining Predictive Distributions”](https://arxiv.org/abs/1106.1638), *Electronic Journal of Statistics* 7 (2013).
[^gpd]: Wang and Tsai, [“Risk Analysis via Generalized Pareto Distributions”](https://pmc.ncbi.nlm.nih.gov/articles/PMC9231421/), *Journal of Business & Economic Statistics* (2022).
[^conditional]: Gneiting and Resin, [“Regression Diagnostics meets Forecast Evaluation”](https://arxiv.org/abs/2108.03210) (2022).
