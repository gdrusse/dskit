# Point-in-time tail-data feature ablation

## TL;DR

Five locally available feature families were added through the standard JSON
pipeline and tested on the frozen 2016--2018 development period. Exact-DTE VRP
was the best average candidate, improving equal-cell CRPS by 0.270% versus the
ADR-0201 incumbent, but it worsened QQQ and SPY upper-tail calibration. Every
candidate failed at least one of the six predeclared tail guards. The selector
therefore refused to freeze a challenger, and 2019--2025 remained unopened.
Keep the ADR-0201 model as the offline research incumbent. This is not a
trading model or authorization to optimize contracts.

## Question and protocol

The estimand is the physical distribution of terminal ETF log return at each
exact listed expiry, using information available by the after-close entry
timestamp. The run changed only the incumbent MLP feature indices inside the
unchanged guarded CDF architecture. It did not change labels, splits, seeds,
calibration, tail blend, weighting, or selection policy.

One preregistered JSON,
`configs/run-predictive-cdf-tail-data.json`, defines five candidates:

- `ohlc_only`: overnight/intraday returns, range, Parkinson variance, jump
  proxy and backward-only 5/22-session summaries;
- `vrp_only`: ATM implied and trailing realized variance on the same requested
  session horizon, their difference and ratio;
- `flow_only`: entry-snapshot volume imbalance, unsigned Greek-times-OI,
  volume-weighted spread, and one/two-observation lagged OI state;
- `cboe_only`: eight additional Cboe series, each joined from a strictly prior
  date with age and missingness; and
- `all_local`: all four available families together.

The macro-event family was not silently approximated. No local point-in-time
calendar with both event date and historical known-at date was found, so the
panel records `available=false` with reason `no local point-in-time macro
calendar` and emits no event predictor.

Search used only 18,616 paired identities from 2016--2018 across all expected
index/exact-DTE cells. Candidate ranking is mean cell CRPS relative to the
horizon-empirical reference, with cells weighted equally. Before ranking, a
candidate must be no farther from 5% than raw `research_incumbent` for both
`below_05` and `above_95` separately for SPY, QQQ and IWM. Tolerance is
`1e-12`. Failure of any comparison makes the candidate infeasible.

## Point-in-time controls

Daily OHLC features use the current after-close bar and prior bars only;
extending the input into the future leaves earlier features unchanged. VRP
uses requested sessions to expiry, never realized actual DTE. The eight new
Cboe closes use the existing strict prior-date as-of join. Flow/Greek inputs
come from the same archived option snapshot; current OI is used only in
unsigned exposure levels, while OI state/change predictors are shifted one and
two observations within symbol/expiry. Actual DTE remains metadata at feature
index 52 and is absent from every candidate selector. Terminal labels and
future dividends are absent from predictors.

The source panel contains 109,355 model rows. Missingness was 0.0823% for
matched VRP and 0.2780% for chain volume; VIX1Y coverage was complete. The
least-covered added Cboe series was VXSLV at 64.79%; every Cboe family member
has an explicit missingness indicator.

IWM dividends are not zero-filled. All 31,946 retained IWM rows remain valid
for raw terminal-close distribution modeling but carry
`strategy_dividend_eligible=0`; SPY has 44,770/44,770 eligible rows and QQQ has
32,639/32,639. This flag is provenance/governance state, not a predictor.

## Development results

Lower score is better. The table reports the equal-cell CRPS ratio to the
horizon-empirical reference and improvement versus the frozen incumbent.
Raw/calibrated values are identical for these uncalibrated candidates.

| Candidate | CRPS ratio | Improvement vs incumbent | Tail guard |
|---|---:|---:|---|
| exact-DTE VRP | 0.975378 | 0.270% | fail: QQQ/SPY upper |
| flow/Greek/liquidity | 0.976594 | 0.146% | fail: IWM lower/upper, QQQ upper, SPY lower |
| OHLC state | 0.978029 | -0.001% | fail: QQQ/SPY lower |
| expanded Cboe | 0.979848 | -0.187% | fail: IWM/QQQ lower |
| all local | 0.981239 | -0.329% | fail: QQQ/SPY lower |
| research incumbent | 0.978021 | reference | frozen reference |

Exact-DTE VRP's guard evidence explains the refusal:

| Index | lower rate candidate/incumbent | pass | upper rate candidate/incumbent | pass |
|---|---:|:---:|---:|:---:|
| IWM | 0.04939 / 0.04898 | yes | 0.02758 / 0.02634 | yes |
| QQQ | 0.06204 / 0.06286 | yes | 0.01876 / 0.01896 | no |
| SPY | 0.05637 / 0.05705 | yes | 0.01673 / 0.01707 | no |

The upper-tail rates are below 5% for both candidate and incumbent. “Worse”
therefore means farther below the target, even though the absolute numerical
change is small. The rule was fixed before outcomes and was not relaxed.

## Decision

No candidate is promoted. The failed select stage is the intended governed
outcome: `ValueError: no candidate satisfies development selection guard`.
Because no model could be frozen, later development/early/middle/late
evaluation and the final evaluator were not run. Reporting later scores for a
post-hoc substitute would violate the protocol.

The useful research signal is that requested-horizon VRP improved average
distribution accuracy but not the conservative upper-tail criterion. A future
ADR may test a predeclared center-only use of VRP that leaves the incumbent
5%/95% mappings fixed, or acquire a genuinely point-in-time macro-event
calendar. Neither is authorized here.

## Reproduction and resource envelope

All commands ran in WSL2 with the project environment, at most two CPU threads,
and no one-off execution script. Generated run artifacts are intentionally
ignored; the tracked JSON and immutable completion record identify them.

- prepare/raw-chain feature cache: 20:31.44 wall, 1,450,464 KiB maximum RSS;
- panel construction smoke: 0:34.28 wall, 1,727,800 KiB maximum RSS;
- development search: 5:12.96 wall, 3,164,844 KiB maximum RSS;
- governed selection refusal: 0:37.97 wall, 1,732,132 KiB maximum RSS.

Search identity hashes are config
`5b1fc2310786241546b8b30a318666fd662d0a53a390bd822e821d3e190d258f`,
panel `4ca51dc0b358601ed8021447e5c3359542b661a39a1ac80ee442a5f1fed459ca`,
and provenance
`caa4308c0e6fbaf3782b9f007769836f2cfb6a706faea22a0bb3056b43dfed11`.
The raw-chain cache covers 109,850 requested snapshots. Focused affected tests:
100 passed; the full suite was not rerun because core code was untouched.
Independent Luna skeptic review concluded C0/M0/m1/n0. Its retained Minor is
that an empty-feasible-set exception is raised before a machine-readable
selection refusal record is written; the immutable raw scores and documented
recomputation preserve the evidence, but a future generic pipeline change
should persist refusal evidence before raising.
