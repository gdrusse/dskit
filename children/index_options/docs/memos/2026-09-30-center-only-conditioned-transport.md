# Center-only conditioned option transport

**Date:** 2026-09-30  
**Decision:** accept `center_index_horizon_transport_prior50` as the
protocol-qualified **offline research incumbent**, while treating the measured
gain as practically negligible. This is not a trading or deployment decision.

## Objective and output

The model estimates the physical CDF of terminal index return at each exact
listed-option expiry. Entry spot converts it directly to a terminal-price CDF,
so any strike can be evaluated with `P(S_T <= K)` and option payoffs can be
integrated over the same curve. It does not choose contracts, size positions,
model fills, or authorize trading.

Each forecast is a monotone 401-knot quantile curve, not seven fixed horizon
diagnostics and not a single volatility point. The requested calendar horizon
and index identity are known at entry. Realized `actual_calendar_dte` is used
only to score exact-DTE cells and is excluded from predictors.

## Sequential experiment

All phases used 2016–2018 development history only, the immutable ADR-0195
panel/provenance, fresh output roots, and the same guard: for each index,
`below_05` and `above_95` must be no farther from 0.05 than the frozen raw
research incumbent. Equal index/actual-DTE-cell CRPS ranks feasible candidates.

- Phase A conditioned the physical transport jointly by index and requested
  horizon at shrinkage strengths 50, 200, and 1000. All candidates were
  rejected. The best improved development CRPS 0.104% but worsened SPY's lower
  and IWM's upper tail rates.
- Phase B separately tested index-only and horizon-only maps at all three
  strengths. No candidate passed all six tail guards, so later history remained
  unopened.
- Phase C fixed strength 50 and confined local transport strictly inside the
  global 5%/95% probability mappings. It compared joint, index-only, and
  horizon-only conditioning. All passed the tail guard; joint conditioning had
  the best development CRPS and was frozen before later evaluation.

Phase C development contains 18,616 forecasts and 133 exact index/DTE cells.
The winner improved equal-cell CRPS **0.101%** versus the research incumbent.
Its six tail rates are identical to the reference by construction and in the
saved scores:

| Index | Lower rate: selected / reference | Upper rate: selected / reference |
|---|---:|---:|
| SPY | 0.057047 / 0.057047 | 0.017069 / 0.017069 |
| QQQ | 0.062861 / 0.062861 | 0.018961 / 0.018961 |
| IWM | 0.048981 / 0.048981 | 0.026343 / 0.026343 |

## Model

The base option curve is the entry-date option-implied terminal-return proxy.
A global training-only PIT quantile map converts its risk-neutral shape toward
the physical distribution. The selected extension also fits maps by requested
calendar horizon and one-hot index. Each local map is shrunk toward the global
map by `n_group / (n_group + 50)`.

Only probabilities strictly between 0.05 and 0.95 can use that local map. At
and beyond those boundaries the global map is copied; local interior values are
clipped between the fixed global boundary values and monotonically rearranged.
That option curve then enters the existing conservative per-index quantile
blend with the empirical/MLP incumbent. Blend weights are selected using only
the preceding calibration period under its tail constraints.

The feature/panel protocol is unchanged. It includes entry-known lagged
returns, realized-volatility and downside/upside summaries, requested horizon
and series-life seasonality, index identity, option-chain level/skew/curvature,
liquidity/depth/open-interest summaries and nodes, volatility indexes and term
structure, VVIX/SKEW/GVZ, rates, credit, dollar, oil and financial-condition
inputs with declared availability lags. No realized expiry information enters
the fit.

## Training and validation

For each forecast year, training observations and labels precede the
calibration and forecast periods; overlapping terminal labels are purged at the
boundary. The global and local PIT transports use training rows only. The outer
blend may consume the preceding calibration labels, never forecast-year labels.
Phase C model choice used only 2016–2018. Frozen evaluations were development
(2016–2018), early (2019–2021), middle (2022–2023), and late (2024–2025).
The final report pairs every model on identical index/date/expiry identities and
weights the 135 index/exact-DTE cells equally.

## Results on reused later history

The 2019–2025 report contains 68,084 paired forecasts, 1,746 entry dates, and
3,495 expiry series. Relative to the ADR-0195 research incumbent, the selected
model's equal-cell CRPS gain is only **0.0146%**. The paired intervals include
zero: 60-date **-0.0768% to 0.1317%** and 120-date **-0.0814% to 0.1162%**.

| Index | N | Selected CRPS | Reference CRPS | Equal-cell skill |
|---|---:|---:|---:|---:|
| SPY | 27,608 | 0.592046 | 0.592538 | +0.0972% |
| QQQ | 21,493 | 0.586195 | 0.585777 | -0.0586% |
| IWM | 18,983 | 0.608790 | 0.608931 | +0.0053% |

The phase skills versus the reference were +0.1012% on development, -0.0007%
on early, -0.0997% on middle, and -0.0028% on late. Later upper-tail rates are
identical; lower-tail rates move only slightly. Condor-loss MSE is marginally
worse (0.076746 versus 0.076699), so this does not establish better strategy
selection despite satisfying the preregistered distributional promotion rule.

## Verification and recommendation

- 187 affected tests pass; focused tests cover shrinkage, unseen fallback,
  mixed batches, center change, fixed 5%/95% boundaries, and monotonicity.
- Luna Phase A/B/C design reviews are C0/M0/m0/n0.
- Every search/evaluation/report stage stayed below 30 minutes and 6 GiB. Phase
  C search took 3:15 at 2.92 GiB; the slowest parallel evaluation took 4:27 and
  peak process RSS was 3.71 GiB; report took 0:37 at 1.52 GiB.
- The 2019–2025 history has been repeatedly inspected. Its intervals are
  pointwise, dependent, and not multiplicity-adjusted.

Carry the selected specification as the protocol-qualified research incumbent
because the preregistered development rule says to do so, but regard it as
effectively tied with ADR-0195. The next valuable evidence is genuinely new
dates and a separately governed strike/payoff optimizer with bid/ask, fees,
exercise/assignment and execution constraints—not another decision made from
the same reused years.
