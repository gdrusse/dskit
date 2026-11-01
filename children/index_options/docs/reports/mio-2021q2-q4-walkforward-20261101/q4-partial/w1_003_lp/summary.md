# MIO 2021 Q4: w1_003_lp — completed method; overall study incomplete

> **Caveats**
>
> - Simulated 2021 Q4 fills against displayed depth. Retrospective development; prior method selection used 2020–2023, so this is not an untouched historical holdout.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Outer portfolio solve: 900-second limit, one thread, relative gap 0.01 and absolute gap $50; a usable incumbent may be returned on timeout. Inner book construction has separate tighter limits.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.
> - PARTIAL OVERALL STUDY: nominal_lp failed at IBM/Kyndryl mixed-deliverable settlement. This report covers only the named completed method; no nominal_lp final totals exist and all-three-method completion is not claimed.
> - Each quarter is an independent $10,000 starting account plus $1,000 every 14 days. Do not compound returns across these independent accounts.
> - The report includes settlement after quarter end. See the quarter-end versus settled-cohort table for the requested quarter boundary.

## What happened

4 round trips over 14 sessions (14 decisions). Decisions ran from 2021-10-01 16:00 to 2021-12-27 16:00 (America/New_York); the data (prices, fills, cash flows) span 2021-10-01 08:00 to 2022-02-18 16:00. Before costs the strategy lost -$8,195.00; fees were $44.85, so net was -$8,239.85. 0% of round trips won. All 4 instruments lost money net of fees; the smallest loss was 2021-11-29:ENPH:2022-01-21 (-$1,926.30 over 1 trips), the largest 2021-10-25:ENPH:2021-12-17 (-$2,231.95 over 1). Worst drawdown of trading P&L: $8,239.85 (82.4% of equity at the peak). External cash flows of +$16,000.00 (7 flows) are in the account balance but are not profit. 19 actions were refused: repriced_shared_cap (9), missing_fill_quote (6), insufficient_fill_size (4). 7,911 actions were skipped: insufficient_eligible_legs (7,910), no_eligible_forecast_expiry (1). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | 8229667565034cb0ca7f13633847bb2414846ceb0ac5a879380a27cd58033565 |
| project | index_options |
| decision window | 2021-10-01 16:00 → 2021-12-27 16:00 (14 sessions) |
| data window | 2021-10-01 08:00 → 2022-02-18 16:00 (36 sessions) |
| time zone | America/New_York |
| instruments | 2021-10-01:MGM:2021-11-19, 2021-10-04:AVAV:2021-11-19, 2021-10-11:BB:2021-11-26, 2021-10-11:CMCSA:2021-10-29, 2021-10-11… |
| score unit | raw |
| currency | USD |
| events | 7,998 |
| run status | ok |
| bar stamps | ts_ms = the instant a mark was observed (UTC ms) |

**Verdict: UNJUDGED**

## Statistics

> **Caveats**
>
> - Simulated 2021 Q4 fills against displayed depth. Retrospective development; prior method selection used 2020–2023, so this is not an untouched historical holdout.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Outer portfolio solve: 900-second limit, one thread, relative gap 0.01 and absolute gap $50; a usable incumbent may be returned on timeout. Inner book construction has separate tighter limits.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.
> - PARTIAL OVERALL STUDY: nominal_lp failed at IBM/Kyndryl mixed-deliverable settlement. This report covers only the named completed method; no nominal_lp final totals exist and all-three-method completion is not claimed.
> - Each quarter is an independent $10,000 starting account plus $1,000 every 14 days. Do not compound returns across these independent accounts.
> - The report includes settlement after quarter end. See the quarter-end versus settled-cohort table for the requested quarter boundary.

| name | value | n | min_n | flag |
|---|---|---|---|---|
| net_pnl | -$8,239.85 | — | 0 | — |
| gross_pnl | -$8,195.00 | — | 0 | — |
| fees | $44.85 | — | 0 | — |
| cost_share | 0.00547 | — | 0 | — |
| twr (at cost) | -53.9% | — | 0 | — |
| max_drawdown (at cost) | $8,239.85 | — | 0 | — |
| max_drawdown_pct (at cost) | 82.4% | — | 0 | — |
| max_drawdown_minutes (at cost) | 202,140 min | — | 0 | — |
| trades | 4 | — | 0 | — |
| hit_rate | 0% | 4 | 30 | insufficient n |
| avg_win | — | 4 | 30 | insufficient n |
| avg_loss | -$2,059.96 | 4 | 30 | insufficient n |
| payoff_ratio | — | 4 | 30 | insufficient n |
| profit_factor | 0 | 4 | 30 | insufficient n |
| trade_mean | -$2,059.96 | 4 | 30 | insufficient n |
| trade_t | -31.3 | 4 | 30 | insufficient n |
| trade_p | 1.000 | 4 | 30 | insufficient n |
| trade_es95 | -$2,231.95 | 4 | 30 | insufficient n |
| days | 14 | — | 0 | — |
| daily_mean_return (at cost) | -5.03% | 14 | 30 | insufficient n |
| daily_sharpe (at cost) | -0.611 | 14 | 30 | insufficient n |
| daily_sharpe_ci_low (at cost) | -0.993 | 14 | 30 | insufficient n |
| daily_sharpe_ci_high (at cost) | -0.229 | 14 | 30 | insufficient n |
| psr (at cost) | 0.000854 | 14 | 30 | insufficient n |
| dsr (at cost) | 0.000854 | 14 | 30 | insufficient n |
| decisions | 14 | — | 0 | — |
| orders | 23 | — | 0 | — |
| fills | 8 | — | 0 | — |
| fill_ratio | 17.4% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 0% | — | 0 | — |
| turnover_per_day (at cost) | 0.0496 | — | 0 | — |
| time_in_market | 86.7% | — | 0 | — |
| avg_exposure (at cost) | 0.242 | — | 0 | — |
| holding_median_minutes | 75,300 min | 4 | 30 | insufficient n |
| holding_p90_minutes | 82,344 min | 4 | 30 | insufficient n |

## Census

| item | count |
|---|---|
| decisions | 14 |
|   action enter | 14 |
|   action exit | 0 |
|   action hold | 0 |
|   action skip | 0 |
|   action refuse | 0 |
| orders | 23 |
| fills | 8 |
| refusals | 19 |
| skips | 7,911 |
|   orders filled | 4 |
|   orders rejected | 19 |
| fills with no order | 4 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | insufficient_eligible_legs | 7,910 |
| refusal | repriced_shared_cap | 9 |
| refusal | missing_fill_quote | 6 |
| refusal | insufficient_fill_size | 4 |
| skip | no_eligible_forecast_expiry | 1 |


**Optimizer:** 14 solves (ok/optimal 14).


**Day-by-day ledger (marked daily curve):** 109 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 3 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 17 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 10 rows in `run-identity.csv`.


**Quarter-end marked account and later settled cohort:** 25 rows in `quarter-boundary.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | 8229667565034cb0ca7f13633847bb2414846ceb0ac5a879380a27cd58033565 | — |
| config_hash | 86e94b58757d8e808edb8d9b02e643da33057c645c40873f335d908c5c2b8d48 | — |
| code commit | — | — |
| code dirty | — | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 0.4480 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=7, decision=14, fill=8, order=23, refusal=19, run_end=1, run_start=1, skip=7911, solve=14 | — |

<!-- artifact-links:begin -->
## Attested Q4 source files

Read-only links to files under /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-tail-v2/mio-2021q4-tail-v2-walkforward-frozen-m53-development-2021-12-31-9add6949. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes |
|---|---|---|---|---|---|
| result | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-tail-v2/mio-2021q4-tail-v2-walk… | present | 8001a7aee4fd3b702568ca1a3e99e58bedc3aa9580352c0b7989d177d9dd691a | 9885084 |
| receipt | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-tail-v2/mio-2021q4-tail-v2-walk… | present | 082ce3aefbee45ee70fc1d7cb3967c1b957f3cb818aa7a495efeab9eef9dbc0f | 531 |
| resolved | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-tail-v2/mio-2021q4-tail-v2-walk… | present | 6f97eca73cb0596435de2513461c8c491e00d2cfb0e64b918743b6701b1043a0 | 308 |
| run_report | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-tail-v2/mio-2021q4-tail-v2-walk… | present | 81b4b215753a419711738fcbc4904a07226133ad7d4b78b527ed586444e12b5f | 513 |

<!-- artifact-links:end -->
