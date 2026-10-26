## TL;DR

The best completed funded MIO experiment is still **M05, uncertainty radius 0.03, restricted to 28–32 days to expiry**. It earned **$6,800.05** on **$35,000 contributed**, ending at **$41,800.05**; extra-spread stress leaves just **$403.80**, and its largest marked drawdown was **65.88%**. The new full-DTE run completed and lost **$7,804.40**, so widening the search has not improved the observed result.

## Where we are

The optimizer can choose option legs and integer quantities across eligible stocks, including buying nothing. The weekly funded simulation, delayed fills, expiry settlement and independent accounting replay now work end to end. Forecast quality and trading profitability are separate: the predictive leaders are M51/M53, but these 2019 trading runs used the base wide-MLP64 fallback, not fitted M51.

We have four completed methods from the earlier ten-method funded study and one completed full-DTE M05 experiment. Six earlier methods remain unscored. **Best** here means the highest realized net profit and time-weighted return among those completed funded experiments, not a validated future winner. Unfunded and perfect-hindsight results are excluded from this comparison.

All trading development here is 2019. We have not opened the 2024–25 holdout or 2026. The earlier ticker screen used 2019 outcomes; the newer screen uses pre-2019 training-period performance, which is still not independent confirmation.

## Best MIO: what the account did

- Initial cash: **$10,000**, followed by **25 deposits of $1,000** every 14 days on trading sessions; total contributions **$35,000**.
- Weekly decisions; flexible option positions; uncertainty radius **0.03**; new-position reserve up to 25% of available funding each week, total reserve up to 100%, no tighter single-name or correlation cap in this variant.
- **17 filled books across 11 of 17 eligible tickers**, **583 gross option contracts**, **$378.95 fees**; ten winning and seven losing books. A book groups one ticker and expiry; contracts count both bought and sold options.
- Realized net profit **$6,800.05**; profit/contributions = $6,800.05 / $35,000 = **19.43%**. The return adjusted for deposit timing is **13.44%**. These are different denominators, not conflicting results.
- Extra-spread stress subtracts **$6,396.25**, leaving **$403.80**. This is a cost sensitivity on the same fills, not a new stress simulation of prices, fills or margin.
- No iron condors were filled. The selected books include long calls, short puts, boxes, a put ratio spread, a synthetic forward and custom combinations. The historical version also used a secondary contract-count tie-break; the current full-DTE version does not.

DTE means calendar days to expiry. The target range was 28–32 at the decision; actual fills below were **30 or 31 DTE** because orders filled the next session. The figures below include all fees.

| Ticker | Filled books | Contracts | Decision DTE | Fill DTE | Net P&L | Stress P&L |
|---|---:|---:|---|---|---:|---:|
| AAPL | 1 | 28 | 32 | 31 | −$45.20 | −$196.70 |
| ACN | 2 | 266 | 31, 32 | 30, 31 | −$644.90 | −$3,496.15 |
| AG | 1 | 7 | 32 | 31 | $380.45 | $354.20 |
| AMGN | 3 | 16 | 32 | 31 | −$233.40 | −$540.40 |
| APO | 1 | 3 | 31 | 30 | −$46.95 | −$74.45 |
| AXP | 1 | 13 | 32 | 31 | $5,668.55 | $5,524.80 |
| BX | 1 | 10 | 32 | 31 | $9.50 | −$59.75 |
| CLF | 2 | 20 | 32 | 31 | $443.00 | $415.75 |
| CPB | 2 | 14 | 32 | 31 | $4,963.90 | $4,862.65 |
| CVS | 2 | 173 | 32 | 31 | −$3,728.45 | −$6,282.20 |
| EQT | 1 | 33 | 32 | 31 | $33.55 | −$103.95 |

Six eligible names had no filled book: **AKAM, CHTR, COF, FSLR, JPM and KKR**. A zero fill count is not evidence of zero predictive skill.

<details>
<summary>All 17 dated trades from the best funded run</summary>

| Decision | Fill | Ticker | Expiry | DTE decision / fill | Contracts | Net P&L | Stress P&L |
|---|---|---|---|---:|---:|---:|---:|
| 2019-01-14 | 2019-01-15 | AMGN | 2019-02-15 | 32 / 31 | 4 | $9.40 | −$47.35 |
| 2019-01-14 | 2019-01-15 | CLF | 2019-02-15 | 32 / 31 | 9 | −$242.85 | −$248.10 |
| 2019-02-11 | 2019-02-12 | AMGN | 2019-03-15 | 32 / 31 | 8 | −$268.20 | −$485.45 |
| 2019-02-19 | 2019-02-20 | APO | 2019-03-22 | 31 / 30 | 3 | −$46.95 | −$74.45 |
| 2019-03-18 | 2019-03-19 | ACN | 2019-04-18 | 31 / 30 | 260 | −$169.00 | −$2,980.25 |
| 2019-05-06 | 2019-05-07 | CPB | 2019-06-07 | 32 / 31 | 12 | $4,888.20 | $4,798.20 |
| 2019-05-13 | 2019-05-14 | AG | 2019-06-14 | 32 / 31 | 7 | $380.45 | $354.20 |
| 2019-07-01 | 2019-07-02 | CVS | 2019-08-02 | 32 / 31 | 57 | $0.95 | −$199.55 |
| 2019-07-15 | 2019-07-16 | AMGN | 2019-08-16 | 32 / 31 | 4 | $25.40 | −$7.60 |
| 2019-07-22 | 2019-07-23 | AAPL | 2019-08-23 | 32 / 31 | 28 | −$45.20 | −$196.70 |
| 2019-08-19 | 2019-08-20 | ACN | 2019-09-20 | 32 / 31 | 6 | −$475.90 | −$515.90 |
| 2019-09-23 | 2019-09-24 | CVS | 2019-10-25 | 32 / 31 | 116 | −$3,729.40 | −$6,082.65 |
| 2019-09-30 | 2019-10-01 | CLF | 2019-11-01 | 32 / 31 | 11 | $685.85 | $663.85 |
| 2019-10-14 | 2019-10-15 | EQT | 2019-11-15 | 32 / 31 | 33 | $33.55 | −$103.95 |
| 2019-10-28 | 2019-10-29 | BX | 2019-11-29 | 32 / 31 | 10 | $9.50 | −$59.75 |
| 2019-11-18 | 2019-11-19 | CPB | 2019-12-20 | 32 / 31 | 2 | $75.70 | $64.45 |
| 2019-11-25 | 2019-11-26 | AXP | 2019-12-27 | 32 / 31 | 13 | $5,668.55 | $5,524.80 |

</details>

Exact strikes, rights, buy/sell quantities and fill premiums are in the [detailed session memo](2026-10-25-prescription-session-results.md#exact-m05-filled-history).

## What the newest full-DTE run tells us

The latest run used a different 17-name pre-2019 screen, all supported positive DTEs through 60, a $50 optimality gap and a 15-minute native solve limit. **All 53 decision weeks and 252 trading sessions completed**: 17 weeks selected orders, 34 timed out and skipped orders, one chose cash and one had no eligible expiry. Only **13 of 127 selected ticker/expiry books filled**; 114 were rejected by quote, size or repriced reserve checks.

It filled **256 contracts across eight tickers**, at **17–53 DTE at decision / 16–52 at fill**. Final equity was **$27,195.60** on $35,000 contributed: **−$7,804.40 net**, **−$10,406.65 stressed**, **−16.29% time-weighted return**, and **62.18% maximum marked drawdown**. All positions settled; the independent replay audit passed.

Two AMZN call books lost **$14,322.60** combined. ACN earned **$6,201.75**; GOOG earned **$728.45**. Every other name together lost $412.00. The [full session memo](2026-10-25-prescription-session-results.md#completed-full-dte-result-2026-10-26) lists every new trade and its exact DTE and legs.

The comparison changes universe, expiry range, tie-break policy and timeout treatment together. It does not isolate the effect of DTE. All 34 timed-out weeks had positive modeled incumbent profits, but their distance from the solver's upper bound exceeded $50; they were skipped under the requested rule, not because the solver found no profitable candidate.

## What deserves attention next

1. **Profit concentration and costs.** CPB's May trade and AXP's November trade contributed $10,556.75 of the best run's $6,800.05 total; the other 15 trades lost $3,756.70. The large drawdown and near-zero stressed profit limit confidence.
2. **Execution realism.** Check large contract counts, boxes, near-zero modeled reserves, quote quality, next-session price changes and rejected fills. Terminal-payoff reserves do not reproduce broker margin or early assignment.
3. **Prediction and risk.** Assess whether the chosen payoff shapes expose a systematic forecast bias, then rank a small number of method/risk improvements without opening holdout data.
4. **Solve speed.** The full-DTE run skipped 34/53 weeks (64.15%). Investigate formulation strength, valid reductions, warm starts and solver settings before treating that restricted set of traded weeks as a broad test of the opportunity set.

## Reproducibility and handoff

This memo was written on October 26, 2026 before wrap, from saved completed results, not a new backtest. The detailed memo retains all attempted variants, failures, limitations and exact trade histories.

Source: [saved research report](/home/russell/.cursor/projects/home-russell-dskit/canvases/index-options-2019-condor-oracle.canvas.tsx), weeklyPriorFormulationRuns v6 derived_old_policy_comparison for the historical leader, and weeklyPortfolio v11 for the new result. Historical comparison identity: dc4063389d2d580229926d3a63e97be5568099da6a6f3f1c4d752702774396dc. V11 identity: 44d615b48bdcdf0e300081ecdedefd0d7b2d0194ab8f10d118d4e4fa29b94580.

Next authorized step: publish both memos, wrap, then launch the requested separate Claude/Fable investigation of method selection, additional MIO work and solver speed.
