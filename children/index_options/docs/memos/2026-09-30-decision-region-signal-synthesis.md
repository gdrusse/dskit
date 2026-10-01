# Decision-region signal synthesis and investigation path

## Decision

There is historical predictive signal near option decision thresholds, but it
is not yet isolated strongly enough to support an optimizer or trading claim.
The strongest signal came from the option-implied transport, not from the final
GPD incumbent. Its average strike and bounded-payoff diagnostics were useful,
but it failed the frozen per-index tail guard. The actual-listed-wing audit then
showed only a small edge over the empirical reference and no incremental GPD
edge over the center incumbent.

The next investigation should therefore optimize and validate predictive
quality on fixed, actual eligible strike and wing regions before changing the
robust optimizer. Optimization can exploit signal; it cannot manufacture it.

## Consolidated evidence

### Broad exact-expiry distribution evidence

The common historical panel contains 68,084 forecasts over 1,746 entry dates,
3,495 expiry series, and 135 index/actual-DTE cells. These observations are not
independent; date-block comparisons are the relevant uncertainty unit.

The option-proxy plus causal PIT transport was the clearest architecture lead
on reused 2019--2025 history:

- equal-cell CRPS skill was +2.75% versus horizon empirical and +1.85% versus
  the prior incumbent;
- its 120-date block intervals were positive against both references;
- strike-Brier skill versus empirical was +4.38% for SPY, +4.70% for QQQ, and
  +0.82% for IWM;
- normalized fixed-condor loss-MSE skill was +8.29% for SPY, +8.34% for QQQ,
  and +3.49% for IWM, or +6.70% overall.

That model failed the development PIT-tail rule for SPY and IWM. It is evidence
of information in the option surface, not an acceptable physical CDF by itself.

The tail-constrained option/incumbent blend retained part of the lead. On the
same reused history it delivered +2.215% CRPS skill and +2.259% strike-Brier
skill versus empirical, while improving full-history tail deviations. The
first frozen candidate still missed the development SPY lower-tail guard. A
subsequent guard-aware selector found a more conservative blend that passed all
six development tail comparisons and improved later CRPS by +0.816% versus its
incumbent. This is the strongest governed evidence that surface information and
tail discipline can coexist, although the years are already inspected.

Conditioned center transport added only +0.0146% later CRPS versus that
incumbent, with intervals crossing zero and marginally worse condor-loss MSE.
The semiparametric GPD layer added +0.08045% CRPS and improved proper tail
scores, but 5% lower-tail hits remained 7.956% overall and 9.794% for QQQ.

### Actual listed decision regions

The bounded actual-chain screen used 60 fixed forecasts, 9,566 chain rows, and
1,824 unique wings. Its refined positive-floor wing scores were:

- horizon empirical: 0.117826;
- center incumbent: 0.117432, about 0.33% better than empirical;
- center GPD: 0.117494, about 0.28% better than empirical but worse than the
  center incumbent for every index.

The center incumbent also improved strike Brier by about 0.31% and condor-loss
MSE by about 3.04% versus empirical on this small screen. That is directionally
positive, but it is too small and too selected to establish dependable local
signal.

The expanded causal study used 180 forecasts, 27,896 chain rows, 41,061
eligible candidates, and 5,373 unique wings. All four causal isotonic
corrections lost to the unchanged GPD on the pre-cutoff wing score. The least
bad correction was 2.25% worse. Final-period diagnostics agreed.

The robust optimizer evaluated 26 SPY/QQQ forecasts. Twenty-one refused for
unsupported radius history, two selected robust no-trade, and three traded.
Mean oracle regret was $2.253681 per share and realized P&L was negative. This
primarily diagnoses insufficient calibration support and a weak decision
sample; it does not overturn or validate the broader predictive evidence.

## What is established

- Historical option-surface information contains measurable expiry-distribution
  signal, especially for SPY and QQQ.
- Signal is larger on synthetic strike/payoff diagnostics than on the first
  actual-listed-wing screen.
- A conservative physical/option blend can satisfy coarse 5%/95% tail guards.
- The GPD tail layer and post-hoc isotonic correction do not improve actual
  decision-region scores.
- Current robust-selection regret cannot be interpreted cleanly because most
  decisions were unsupported and only three trades occurred.

## What is not established

- Fresh, prospective skill on actual listed SPY/QQQ strike regions.
- Reliable conditional calibration at candidate short-strike boundaries.
- Positive expected value after bid/ask, fees, early exercise, and selection.
- Sufficient radius coverage for a useful robust policy.
- Any strategy eligibility for IWM while dividend windows remain unknown.

## Signal-first investigation

### Phase 1: build the complete decision-region benchmark

Use all eligible SPY/QQQ strike events and unique wings on the chronological
development archive. Compute each unique spread integral once, then reuse it
across condors so the full audit remains below 30 minutes and 6 GiB. Give each
forecast total weight one, deduplicate wings within forecast, average within
entry/expiry and side, and then apply fixed date/index/tenor weights. Keep a
small positive global score floor.

Primary metrics are actual-strike Brier score and positive-floor wing-weighted
CRPS. Retain full CRPS, separate put/call signed bias, tail coverage, capped
loss MAE/MSE, and candidate-ranking regret as diagnostics. Do not use premiums
or optimizer-selected strikes to define score weights.

### Phase 2: run one bounded architecture comparison

Compare only four preregistered raw-CDF candidates on identical rows:

1. horizon empirical;
2. the governed conservative physical incumbent;
3. raw option-implied transport;
4. a monotone tail-constrained physical/option blend selected directly by the
   actual-wing proper score.

This isolates whether the large option-transport diagnostic lead survives on
real listed strikes and whether blending can preserve it without tail failure.
Do not reopen a broad architecture zoo.

### Phase 3: add features only after the architecture result

If a candidate clears Phase 2, run chronological forward additions of the
already available point-in-time families: OHLC state, requested-horizon VRP,
option flow/liquidity/Greeks, and prior-date Cboe state. Add one family at a
time, retain it only on incremental actual-wing score, and keep tail checks as
acceptance constraints. This corrects the earlier bundled-feature test: a
failed combined model does not prove each feature family lacks local value.

### Phase 4: freeze and validate

Use strict settlement chronology, same-date batching, exact expiry identity,
and nested ownership of model fit, calibration, and selection. Report paired
moving-date-block intervals and effective block counts. Freeze one model only
if its actual-wing improvement is positive overall and for both SPY and QQQ,
its block interval excludes zero, its signed put/call biases are acceptable,
and no frozen tail guard regresses.

All 2019--2025 results remain descriptive because they have been repeatedly
inspected. The first confirmatory result must come from the separately frozen
prospective recorder or another untouched chronological period with timestamped
chain and spot quotes.

### Phase 5: return to optimization

Only after Phase 4 qualifies a CDF should robust condor selection resume.
Calibrate local uncertainty from strictly prior settled strike residuals,
compare nominal, W1-only, local-band-only, and joint ambiguity sets, and require
useful support coverage. Decompose regret into unsupported refusal, robust
no-trade, strike selection, and distribution error before changing radius
conservatism. The optimizer remains a downstream consumer of the frozen CDF.

## Immediate recommendation

Implement one standard JSON for Phases 1--2 first. Reuse the existing
decision-region audit, option transport, guard-aware selector, exact wing
integration, and completion manifests. The experiment should end with either a
qualified local-signal model or an explicit refusal. Do not tune the robust
optimizer again until that result exists.

## Consolidation record

Parallel strategy work already occupied ADR-0193 through ADR-0197 and journal
A0611--A0612 on `main`. The CDF continuation was collision-safely renumbered to
ADR-0198 through ADR-0206 and A0613 through A0620. Both histories are retained;
no result was overwritten. The affected merged CDF/observation suites pass 225
tests with 26 dependency warnings.
