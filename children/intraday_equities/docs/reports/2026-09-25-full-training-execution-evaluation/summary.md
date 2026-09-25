# Full-training production-equivalent execution (2022-09-09 to 2025-10-16)

## What happened

457 round trips over 47 sessions (50 decisions). Decisions ran from 2022-11-11 11:01 to 2025-05-07 14:02 (America/New_York); the data (prices, fills, cash flows) span 2022-09-09 09:30 to 2025-10-16 09:30. Before costs the strategy made +$2,949.40; fees were $1,463.24, so net was +$1,486.16 (costs were 0.496x gross). Biggest driver: the forecasts' edge; fees took 49.6% of the gross. 50.5% of round trips won; the average win ($43.18) was larger than the average loss ($37.56). Best instrument LITE (+$706.82 over 71 trips); worst CIEN (-$206.50 over 7). Worst drawdown of trading P&L: $792.46 (19.2% of equity at the peak). External cash flows of +$16,580.00 (780 flows) are in the account balance but are not profit. 50 actions were refused: insufficient_cash (50). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

### How to read this report

- Money is in the account currency. Trading P&L never includes deposits or withdrawals; account equity does, so compare P&L, not the balance.
- Gross means before fees and costs; net means after. A round trip is one entry matched FIFO to the exit that closed it.
- Forecasts, edges and thresholds print in the unit the run declared (a return in basis points: 1 bp = 0.01%).
- The verdict applies pre-registered criteria. INCONCLUSIVE means the sample is below the criterion's min_n, not that the result is borderline; statistics flagged 'insufficient n' are shown but should not be trusted.
- Charts collapse market-closed time: a thin vertical break with a date marks each new session. Hover a marker for the decision behind it: its reason, forecast, rank and edge.
- Every decision is in decisions.csv, every round trip in trades.csv and every event in events.jsonl; the page thins what it draws and says so where it does.

## Run

| field | value |
|---|---|
| run | intraday-equities-development-simulation-2026-09-24-84740cdd |
| project | intraday_equities |
| decision window | 2022-11-11 11:01 → 2025-05-07 14:02 (47 sessions) |
| data window | 2022-09-09 09:30 → 2025-10-16 09:30 (779 sessions) |
| time zone | America/New_York |
| instruments | ADBE, CIEN, LITE, LULU, MSTR, NOW, TER |
| score unit | raw |
| currency | USD |
| events | 1,796 |
| run status | ok |
| bar stamps | ts_ms = the instant a mark was observed (UTC ms) |

**Verdict: UNJUDGED**

## Statistics

| name | value | n | min_n | flag |
|---|---|---|---|---|
| net_pnl | $1,486.16 | — | 0 | — |
| gross_pnl | $2,949.40 | — | 0 | — |
| fees | $1,463.24 | — | 0 | — |
| cost_share | 0.496 | — | 0 | — |
| twr | 15.5% | — | 0 | — |
| max_drawdown | $792.46 | — | 0 | — |
| max_drawdown_pct | 19.2% | — | 0 | — |
| max_drawdown_minutes | 525,841 min | — | 0 | — |
| trades | 457 | — | 0 | — |
| hit_rate | 50.5% | 457 | 30 | — |
| avg_win | $43.18 | 457 | 30 | — |
| avg_loss | -$37.56 | 457 | 30 | — |
| payoff_ratio | 1.15 | 457 | 30 | — |
| profit_factor | 1.18 | 457 | 30 | — |
| trade_mean | $3.25 | 457 | 30 | — |
| trade_t | 1.05 | 457 | 30 | — |
| trade_p | 0.147 | 457 | 30 | — |
| trade_es95 | -$143.81 | 457 | 30 | — |
| days | 778 | — | 0 | — |
| daily_mean_return | 0.0200% | 778 | 30 | — |
| daily_sharpe | 0.0368 | 778 | 30 | — |
| daily_sharpe_ci_low | -0.0331 | 778 | 30 | — |
| daily_sharpe_ci_high | 0.107 | 778 | 30 | — |
| psr | 0.849 | 778 | 30 | — |
| dsr | 0.849 | 778 | 30 | — |
| decisions | 50 | — | 0 | — |
| orders | 0 | — | 0 | — |
| fills | 914 | — | 0 | — |
| fill_ratio | — | — | 0 | — |
| refusal_rate | 100% | — | 0 | — |
| skip_rate | 0% | — | 0 | — |
| turnover_per_day | 0.994 | — | 0 | — |
| time_in_market | 27.1% | — | 0 | — |
| avg_exposure | 0.252 | — | 0 | — |
| holding_median_minutes | 6.00 min | 457 | 30 | — |
| holding_p90_minutes | 8.00 min | 457 | 30 | — |

## Census

| item | count |
|---|---|
| decisions | 50 |
|   action enter | 0 |
|   action exit | 0 |
|   action hold | 0 |
|   action skip | 0 |
|   action refuse | 50 |
| orders | 0 |
| fills | 914 |
| refusals | 50 |
| skips | 0 |
| fills with no order | 914 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| refusal | insufficient_cash | 50 |


**Optimizer:** No optimizer solves recorded.


**Inference:** No forecast could be scored: 0 scored candidate(s) have no outcome event. A producer logs one outcome per candidate (decision_id, instrument, realized) once the target is known.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | intraday-equities-development-simulation-2026-09-24-84740cdd | — |
| config_hash | 6c2523c860048a93e2211ea9676e7e5c085ce30f947b73ff59a61208ae205f1a | — |
| code commit | bb1ef42 | — |
| code dirty | — | — |
| trials | 1 | — |
| python | — | — |
| platform | — | — |
| packages | 0 | — |
| wall time | — | — |
| events | cashflow=780, decision=50, fill=914, refusal=50, run_end=1, run_start=1 | — |
