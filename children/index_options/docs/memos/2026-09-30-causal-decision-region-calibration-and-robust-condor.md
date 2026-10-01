# Causal decision-region calibration and robust condor selection

## Decision

Keep `center_gpd_05_prior25` unchanged as the offline CDF research model. Four
causal strike-event corrections were trained, but none improved the frozen
decision-region score. The exact Wasserstein optimizer ran in a separate final
period: only five of 26 SPY/QQQ forecasts had a supported radius, three traded,
two chose robust no-trade, and 21 refused for insufficient or uncovered radius
history. Mean realized P&L was `-$0.139661` per share (`-$13.9661` per
100-share contract), while mean oracle regret was `$2.253681` per share. This is a completed historical research run,
not a deployable model or trading result.

## What was trained

For each raw GPD CDF forecast, the challenger fits a monotone map from the
model's probability at every eligible option strike to the realized event
`terminal_price <= strike`. The map uses only outcomes whose settlement date
is strictly earlier than the new quote date. Forecasts sharing a quote date are
batched, so they cannot train one another. Events are pooled only within index
and requested-tenor band. Every forecast contributes total weight one,
regardless of its number of listed strikes.

Four identity-shrinkage strengths were frozen before the run: `0`, `25`,
`100`, and `400`. The fitted isotonic map is coherent, monotone, and anchored
at probabilities zero and one. Sparse histories refuse the correction. The
resulting model still outputs a complete terminal-price CDF; it is not a point
forecast. At any strike `K`, it returns `P(S_T <= K)`, and expected capped wing
losses are computed by integrating that CDF over the actual listed wing.

## Data and protocol

The raw forecast archive is the ADR-0197 GPD development archive. A
deterministic screen selected 60 evenly spaced 2018 forecast identities for
each of SPY, QQQ, and IWM: 180 forecasts, 27,896 chain rows, 41,061 eligible
condor/model rows, and 5,373 unique within-forecast wing templates. The chain
quotes contain dates but no source timestamps, so every result is an
after-close diagnostic and is not executable.

The correction-selection cutoff was `2018-07-01`; radius calibration begins
then, and final optimization begins `2018-10-01`. These three periods are
disjoint, so residuals used to select a radius never participated in selecting
the correction. Training is expanding-window and causal inside 2018. Forty forecast identities had enough settled
history to pair all four corrected candidates with the unchanged model before
the cutoff. Selection minimized the positive-floor, actual eligible-wing
proper score, subject to each index's 5%/95% tail deviations being no worse
than the unchanged model by more than `0.01`. Full-CDF CRPS, strike Brier, and
tail rates were retained as diagnostics. No post-cutoff result could change
the selected correction.

This is deliberately not called fresh validation: 2018 has already been used
in prior model development, and the 180 rows are a bounded deterministic
sample rather than the full archive. The 26 final-period optimizer cases are a
held-back segment for this protocol only.

## Correction results

The unchanged model's paired pre-cutoff wing score was `0.0754597`.

| Candidate | Wing score | Relative to unchanged | Tail guard | Outcome |
|---|---:|---:|---|---|
| unchanged GPD | 0.0754597 | — | reference | retained |
| prior 0 | 0.1283378 | 70.07% worse | fail | reject |
| prior 25 | 0.0819987 | 8.67% worse | pass | reject |
| prior 100 | 0.0781330 | 3.54% worse | pass | reject |
| prior 400 | 0.0771561 | 2.25% worse | pass | reject |

The unshrunk map materially worsened upper-tail rates. The three shrunk maps
passed the frozen tail rule but did not improve the primary wing score.
Final-period diagnostics point in the same direction: the unchanged model's
wing score was `0.117582`, versus `0.117918` for the least-bad corrected
challenger (`prior_400`). There
is therefore no evidence that this local isotonic correction improves the CDF.

## Robust optimization

For each SPY/QQQ forecast, the optimizer enumerated the complete configured
one-lot condor universe and included no trade. Net credit used executable-side
bid/ask marks and four leg fees. For radii `0`, `0.001`, `0.0025`, `0.005`, and
`0.01` in spot units, it solved the exact finite-grid Wasserstein worst-case
expected terminal loss and maximized:

`net_credit - worst_case_expected_terminal_loss`.

The radius at each forecast used only strictly settled residuals dated on or
after July 1 for the same index/requested-tenor band; correction-selection
residuals were excluded. Earlier final-period residuals could causally update
later decisions once settled. A 500-replicate moving four-date block
upper bound used a Bonferroni-adjusted 5% family error. The smallest radius
whose upper residual bound was nonpositive was selected; unsupported cases
refused. Among supported final forecasts, two selected `0.001` and three
selected `0.0025`.

Across 26 final-period SPY/QQQ forecasts, three trades were selected, two were
robust no-trades, and 21 refused for insufficient or uncovered history. SPY
had one trade and mean P&L `-$0.156154` per forecast; QQQ had two and mean P&L
`-$0.123169`. Among trades, mean P&L was `-$1.210399`, median `-$2.030000`,
minimum `-$3.249526`, and maximum `+$1.648329` per share. This tiny sample and
negative result do not establish economic value.

IWM was modeled but excluded from strategy selection because its dividend
windows are unknown in the local archive. No null dividend was converted to
zero. American early-exercise charges are realized diagnostics, not predictors
or decision-time inputs.

## Resource and verification record

- Decision preparation: 180 forecasts and 27,896 chain rows; 1.47 seconds,
  660 MiB peak RSS.
- Decision audit: 180 forecasts and 41,061 candidate rows; 16.45 seconds,
  499 MiB peak RSS.
- Causal correction training: 900 model/forecast rows and 7,931 strike events;
  54.51 seconds, 386 MiB peak RSS.
- Exact robust optimization: 9 minutes 59.46 seconds, 361 MiB peak RSS.
- Focused tests: 150 passed. No full repository suite was run because the
  changes are confined to the option CDF/decision pipeline.

Canonical generated artifacts are under
`pipeline_runs/cdf_gpd_decision_regions_calibration_r3_20260930` and
`pipeline_runs/cdf_causal_robust_condor_calibration_r3_20260930`; they remain
ignored. The JSON documents, source hashes, correction selection, policies,
decisions, implementation digest, and report bind the run.

## Recommendation

Do not promote the correction and do not trade this result. Retain the GPD CDF
as the offline research incumbent and retain the robust optimizer as a working
decision layer. The next useful evidence is a locked prospective forecast log
with timestamped chain quotes and reliable IWM dividends. Engineering work can
also make the exact W1 enumeration faster through provably safe caching or
upper-bound pruning, but speed alone will not improve predictive skill.
