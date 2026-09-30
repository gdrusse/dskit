# Guard-aware CDF selection

**Date:** 2026-09-30
**Decision:** promote the very-conservative tail blend as the new **offline
research incumbent**. This is not a trading or deployment decision.

## Outcome

The guard-aware selector rejected the broad blend (two failed index/tail
comparisons) and fine-tail blend (one failed comparison: SPY lower tail). It
froze `tail_blend_very_conservative`, the lowest-development-CRPS candidate
that passed all six index/tail guards.

On development (2016–2018, 18,616 forecasts, 133 exact index/DTE cells), the
selected model improved equal-cell CRPS **1.122%** versus the incumbent. All six
absolute PIT-tail deviations improved:

| Index | Lower rate: selected / incumbent | Upper rate: selected / incumbent |
|---|---:|---:|
| SPY | 0.05705 / 0.05738 | 0.01707 / 0.01606 |
| QQQ | 0.06286 / 0.06451 | 0.01896 / 0.01649 |
| IWM | 0.04898 / 0.04816 | 0.02634 / 0.02511 |

The guard compares absolute distance from 0.05. Thus the higher upper rates
are improvements because both models under-shoot 0.05 on development.

On reused 2019–2025 evaluation history (68,084 forecasts, 1,746 entry dates,
3,495 expiry series), equal-cell CRPS skill versus the incumbent was **0.816%**.
Paired intervals exclude zero: 60-date 0.394%–1.400%; 120-date
0.395%–1.373%.

| Index | Selected CRPS | Incumbent CRPS | Skill | Selected lower / upper rate | Incumbent lower / upper rate |
|---|---:|---:|---:|---:|---:|
| SPY | 0.592538 | 0.599848 | 1.219% | 0.06509 / 0.05042 | 0.06632 / 0.05104 |
| QQQ | 0.585777 | 0.591554 | 0.977% | 0.09845 / 0.06909 | 0.09896 / 0.07295 |
| IWM | 0.608931 | 0.611385 | 0.401% | 0.07960 / 0.05510 | 0.07965 / 0.05795 |

All three indexes improve CRPS and both later-period tail deviations. The
option transport alone remains stronger on average but lacks the selected
blend's governed development tail qualification.

## Model and procedure

The distribution is the ADR-0194 quantile blend. Per index and forecast year,
calibration selects option weights at probability anchors 0.05, 0.50 and 0.95.
This conservative grid caps tail weights at 0.10 and center weights at 0.50.
Typical selected weights were `(0.10, 0.50, 0.10)`; lower weights appeared when
the calibration tail guard required them.

`configs/run-predictive-cdf-guard-aware.json` declares the selection guard:
raw incumbent reference, `below_05` and `above_95`, target 0.05 and tolerance
`1e-12`. Search uses development only. The selector first filters every
model/variant by all per-index guard comparisons, then applies the unchanged
equal-cell CRPS ranking. It serializes every mean, deviation and pass/fail in
`selection/selected.json`. The winner is frozen before later evaluation.

The experiment reused the identical ADR-0194 prepared panel and provenance
hashes; it used a fresh artifact root and did not overwrite prior evidence.
Search completed in 2:34 at 2.89 GiB peak RSS. Evaluation stages were at most
2:10 and 3.82 GiB. All remained below 30 minutes and 6 GiB.

## Verification and limits

- 126 affected tests passed, including end-to-end guard evidence persistence
  and no-feasible refusal.
- Luna design/selection review: C0/M0/m0/n0 after verifying panel/provenance
  identity, fresh outputs, all six comparisons and the frozen winner.
- 2019–2025 history was already inspected; it is descriptive, not a fresh
  holdout. Intervals are pointwise and not multiplicity-adjusted.
- PIT uses the existing deterministic right-CDF convention for atoms.
- No transaction costs, execution, strike optimization, sizing or trading
  performance is established.

## Recommendation

Use `tail_blend_very_conservative` as the governed offline research reference
for the next CDF/condor experiment. Keep the prior incumbent as a reported
control. Do not call the model trading-ready; the next meaningful gate is
prospective new-date validation followed by a separately governed option
selection and execution-cost study.
