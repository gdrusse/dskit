# MIO 2021 Q3: w1_003_lp — complete coverage, quarter-start M53

> **Caveats**
>
> - Simulated 2021 Q3 fills against displayed depth. Retrospective development; prior method selection used 2020–2023, so this is not an untouched historical holdout.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Outer portfolio solve: 900-second limit, one thread, relative gap 0.01 and absolute gap $50; a usable incumbent may be returned on timeout. Inner book construction has separate tighter limits.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.
> - Each quarter is an independent $10,000 starting account plus $1,000 every 14 days. Do not compound returns across these independent accounts.
> - The report includes settlement after quarter end. See the quarter-end versus settled-cohort table for the requested quarter boundary.

## What happened

6 round trips over 14 sessions (14 decisions). Decisions ran from 2021-07-01 16:00 to 2021-09-27 16:00 (America/New_York); the data (prices, fills, cash flows) span 2021-07-01 08:00 to 2021-10-29 16:00. Before costs the strategy lost -$3,600.00; fees were $36.40, so net was -$3,636.40. 16.7% of round trips won; the average win ($33.40) was smaller than the average loss ($733.96). Best instrument 2021-09-13:ABUS:2021-10-15 (+$33.40 over 1 trips); worst 2021-07-12:BB:2021-08-20 (-$2,130.65 over 1). Worst drawdown of trading P&L: $3,636.40 (36.4% of equity at the peak). External cash flows of +$16,000.00 (7 flows) are in the account balance but are not profit. 15 actions were refused: repriced_shared_cap (6), missing_fill_quote (5), insufficient_fill_size (4). 8,018 actions were skipped: insufficient_eligible_legs (8,017), no_eligible_forecast_expiry (1). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | 9a2412be0ce2e686404e3915e23e47d6a1d1e30ba57738bfb12490680ddd39eb |
| project | index_options |
| decision window | 2021-07-01 16:00 → 2021-09-27 16:00 (14 sessions) |
| data window | 2021-07-01 08:00 → 2021-10-29 16:00 (37 sessions) |
| time zone | America/New_York |
| instruments | 2021-07-01:WEN:2021-08-20, 2021-07-06:GEO:2021-08-20, 2021-07-06:WEN:2021-08-20, 2021-07-12:BB:2021-08-20, 2021-07-19:BB… |
| score unit | raw |
| currency | USD |
| events | 8,103 |
| run status | ok |
| bar stamps | ts_ms = the instant a mark was observed (UTC ms) |

**Verdict: UNJUDGED**

## Statistics

> **Caveats**
>
> - Simulated 2021 Q3 fills against displayed depth. Retrospective development; prior method selection used 2020–2023, so this is not an untouched historical holdout.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Outer portfolio solve: 900-second limit, one thread, relative gap 0.01 and absolute gap $50; a usable incumbent may be returned on timeout. Inner book construction has separate tighter limits.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.
> - Each quarter is an independent $10,000 starting account plus $1,000 every 14 days. Do not compound returns across these independent accounts.
> - The report includes settlement after quarter end. See the quarter-end versus settled-cohort table for the requested quarter boundary.

| name | value | n | min_n | flag |
|---|---|---|---|---|
| net_pnl | -$3,636.40 | — | 0 | — |
| gross_pnl | -$3,600.00 | — | 0 | — |
| fees | $36.40 | — | 0 | — |
| cost_share | 0.0101 | — | 0 | — |
| twr (at cost) | -25.6% | — | 0 | — |
| max_drawdown (at cost) | $3,636.40 | — | 0 | — |
| max_drawdown_pct (at cost) | 36.4% | — | 0 | — |
| max_drawdown_minutes (at cost) | 173,280 min | — | 0 | — |
| trades | 6 | — | 0 | — |
| hit_rate | 16.7% | 6 | 30 | insufficient n |
| avg_win | $33.40 | 6 | 30 | insufficient n |
| avg_loss | -$733.96 | 6 | 30 | insufficient n |
| payoff_ratio | 0.0455 | 6 | 30 | insufficient n |
| profit_factor | 0.00910 | 6 | 30 | insufficient n |
| trade_mean | -$606.07 | 6 | 30 | insufficient n |
| trade_t | -1.84 | 6 | 30 | insufficient n |
| trade_p | 0.938 | 6 | 30 | insufficient n |
| trade_es95 | -$2,130.65 | 6 | 30 | insufficient n |
| days | 14 | — | 0 | — |
| daily_mean_return (at cost) | -1.98% | 14 | 30 | insufficient n |
| daily_sharpe (at cost) | -0.420 | 14 | 30 | insufficient n |
| daily_sharpe_ci_low (at cost) | -0.697 | 14 | 30 | insufficient n |
| daily_sharpe_ci_high (at cost) | -0.143 | 14 | 30 | insufficient n |
| psr (at cost) | 0.00147 | 14 | 30 | insufficient n |
| dsr (at cost) | 0.00147 | 14 | 30 | insufficient n |
| decisions | 14 | — | 0 | — |
| orders | 21 | — | 0 | — |
| fills | 12 | — | 0 | — |
| fill_ratio | 28.6% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 0% | — | 0 | — |
| turnover_per_day (at cost) | 0.0249 | — | 0 | — |
| time_in_market | 73.3% | — | 0 | — |
| avg_exposure (at cost) | 0.0857 | — | 0 | — |
| holding_median_minutes | 55,110 min | 6 | 30 | insufficient n |
| holding_p90_minutes | 64,470 min | 6 | 30 | insufficient n |

## Census

| item | count |
|---|---|
| decisions | 14 |
|   action enter | 14 |
|   action exit | 0 |
|   action hold | 0 |
|   action skip | 0 |
|   action refuse | 0 |
| orders | 21 |
| fills | 12 |
| refusals | 15 |
| skips | 8,018 |
|   orders filled | 6 |
|   orders rejected | 15 |
| fills with no order | 6 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | insufficient_eligible_legs | 8,017 |
| refusal | repriced_shared_cap | 6 |
| refusal | missing_fill_quote | 5 |
| refusal | insufficient_fill_size | 4 |
| skip | no_eligible_forecast_expiry | 1 |


**Optimizer:** 14 solves (ok/optimal 14).


**Day-by-day ledger (marked daily curve):** 110 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 6 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 13 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 10 rows in `run-identity.csv`.


**Quarter-end marked account and later settled cohort:** 25 rows in `quarter-boundary.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | 9a2412be0ce2e686404e3915e23e47d6a1d1e30ba57738bfb12490680ddd39eb | — |
| config_hash | dc5fbd1a41911a6b4f3650221394a749b62a35fadd93387dccd8a68aec5e6461 | — |
| code commit | — | — |
| code dirty | — | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 0.4680 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=7, decision=14, fill=12, order=21, refusal=15, run_end=1, run_start=1, skip=8018, solve=14 | — |

<!-- artifact-links:begin -->
## Attested Q3 source files

Read-only links to files under /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q3/mio-2021q3-complete-walkforward-frozen-m53-development-2021-09-30-5d31e90e. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes |
|---|---|---|---|---|---|
| result | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q3/mio-2021q3-complete-walkforward… | present | 378da46164741d4bfd6ede6c566439501bba4ce42b384d47f2d41a02b8213786 | 14666239 |
| receipt | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q3/mio-2021q3-complete-walkforward… | present | bd74268d9b07fa96d62dcc16e36b61f11ae75a63fab6a9749ea2bf008fa5e9dd | 533 |
| resolved | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q3/mio-2021q3-complete-walkforward… | present | 5e09d36e2281c53f6a03e2fd4aa1865a16eba460feac52f3f1b105c3f4bc8582 | 308 |
| run_report | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q3/mio-2021q3-complete-walkforward… | present | 2668eeb9a8646d47e20abadcd98fcf2b81f65adc8ee2cc27cb3e98398ca4ba91 | 515 |

<!-- artifact-links:end -->
