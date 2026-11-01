# MIO 2021 Q2: w1_003_shock10_lp — complete coverage, quarter-start M53

> **Caveats**
>
> - Simulated 2021 Q2 fills against displayed depth. Retrospective development; prior method selection used 2020–2023, so this is not an untouched historical holdout.
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

22 round trips over 14 sessions (14 decisions). Decisions ran from 2021-04-01 16:00 to 2021-06-28 16:00 (America/New_York); the data (prices, fills, cash flows) span 2021-04-01 08:00 to 2021-08-20 16:00. Before costs the strategy made +$2,043.00; fees were $189.15, so net was +$1,853.85 (costs were 0.0926x gross). 31.8% of round trips won; the average win ($1,389.03) was larger than the average loss ($524.62). Best instrument 2021-04-26:GEO:2021-06-18 (+$4,284.55 over 1 trips); worst 2021-06-28:WEN:2021-08-20 (-$1,696.80 over 1). Worst drawdown of trading P&L: $5,133.25 (51.3% of equity at the peak). External cash flows of +$16,000.00 (7 flows) are in the account balance but are not profit. 13 actions were refused: missing_fill_quote (9), repriced_shared_cap (3), insufficient_fill_size (1). 7,892 actions were skipped: insufficient_eligible_legs (7,892). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | 54da7ca04edb857c76c0c1a899d1744b615ec1cd5ae265fe105ae3dd8bbb7d92 |
| project | index_options |
| decision window | 2021-04-01 16:00 → 2021-06-28 16:00 (14 sessions) |
| data window | 2021-04-01 08:00 → 2021-08-20 16:00 (43 sessions) |
| time zone | America/New_York |
| instruments | 2021-04-01:DKS:2021-05-21, 2021-04-01:PBR:2021-05-21, 2021-04-05:BB:2021-05-21, 2021-04-05:DVN:2021-05-21, 2021-04-12:BB… |
| score unit | raw |
| currency | USD |
| events | 8,021 |
| run status | ok |
| bar stamps | ts_ms = the instant a mark was observed (UTC ms) |

**Verdict: UNJUDGED**

## Statistics

> **Caveats**
>
> - Simulated 2021 Q2 fills against displayed depth. Retrospective development; prior method selection used 2020–2023, so this is not an untouched historical holdout.
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
| net_pnl | $1,853.85 | — | 0 | — |
| gross_pnl | $2,043.00 | — | 0 | — |
| fees | $189.15 | — | 0 | — |
| cost_share | 0.0926 | — | 0 | — |
| twr (at cost) | 3.18% | — | 0 | — |
| max_drawdown (at cost) | $5,133.25 | — | 0 | — |
| max_drawdown_pct (at cost) | 51.3% | — | 0 | — |
| max_drawdown_minutes (at cost) | 100,800 min | — | 0 | — |
| trades | 22 | — | 0 | — |
| hit_rate | 31.8% | 22 | 30 | insufficient n |
| avg_win | $1,389.03 | 22 | 30 | insufficient n |
| avg_loss | -$524.62 | 22 | 30 | insufficient n |
| payoff_ratio | 2.65 | 22 | 30 | insufficient n |
| profit_factor | 1.24 | 22 | 30 | insufficient n |
| trade_mean | $84.27 | 22 | 30 | insufficient n |
| trade_t | 0.287 | 22 | 30 | insufficient n |
| trade_p | 0.388 | 22 | 30 | insufficient n |
| trade_es95 | -$1,600.55 | 22 | 30 | insufficient n |
| days | 26 | — | 0 | — |
| daily_mean_return (at cost) | 1.19% | 26 | 30 | insufficient n |
| daily_sharpe (at cost) | 0.0716 | 26 | 30 | insufficient n |
| daily_sharpe_ci_low (at cost) | -0.279 | 26 | 30 | insufficient n |
| daily_sharpe_ci_high (at cost) | 0.422 | 26 | 30 | insufficient n |
| psr (at cost) | 0.655 | 26 | 30 | insufficient n |
| dsr (at cost) | 0.655 | 26 | 30 | insufficient n |
| decisions | 14 | — | 0 | — |
| orders | 35 | — | 0 | — |
| fills | 44 | — | 0 | — |
| fill_ratio | 62.9% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 0% | — | 0 | — |
| turnover_per_day (at cost) | 0.0851 | — | 0 | — |
| time_in_market | 92.6% | — | 0 | — |
| avg_exposure (at cost) | 0.489 | — | 0 | — |
| holding_median_minutes | 64,470 min | 22 | 30 | insufficient n |
| holding_p90_minutes | 84,342 min | 22 | 30 | insufficient n |

## Census

| item | count |
|---|---|
| decisions | 14 |
|   action enter | 14 |
|   action exit | 0 |
|   action hold | 0 |
|   action skip | 0 |
|   action refuse | 0 |
| orders | 35 |
| fills | 44 |
| refusals | 13 |
| skips | 7,892 |
|   orders filled | 22 |
|   orders rejected | 13 |
| fills with no order | 22 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | insufficient_eligible_legs | 7,892 |
| refusal | missing_fill_quote | 9 |
| refusal | repriced_shared_cap | 3 |
| refusal | insufficient_fill_size | 1 |


**Optimizer:** 14 solves (ok/optimal 14).


**Day-by-day ledger (marked daily curve):** 109 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 18 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 13 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 10 rows in `run-identity.csv`.


**Quarter-end marked account and later settled cohort:** 25 rows in `quarter-boundary.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | 54da7ca04edb857c76c0c1a899d1744b615ec1cd5ae265fe105ae3dd8bbb7d92 | — |
| config_hash | 6bf2990bbe7ba06c83b397a2c109d07f612ece3130855bf5629772cf7b3fceff | — |
| code commit | — | — |
| code dirty | — | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 0.9120 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=7, decision=14, fill=44, order=35, refusal=13, run_end=1, run_start=1, skip=7892, solve=14 | — |

<!-- artifact-links:begin -->
## Attested Q2 source files

Read-only links to files under /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q2/mio-2021q2-complete-walkforward-frozen-m53-development-2021-06-30-7ef19de4. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes |
|---|---|---|---|---|---|
| result | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q2/mio-2021q2-complete-walkforward… | present | d34467f5bef67e3ec136be0ed166782a1876fc7dd36552cef0cf5f8fce63949d | 16140337 |
| receipt | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q2/mio-2021q2-complete-walkforward… | present | ecae3c578a2faa466e9d582e216249995e034031dabc2f3164bbec162e528148 | 533 |
| resolved | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q2/mio-2021q2-complete-walkforward… | present | 65f39bc09d3ac1451cea59b585baca8691972f5c52d62c5f1400a9f24bd4c1ea | 308 |
| run_report | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q2/mio-2021q2-complete-walkforward… | present | 82b46272cb21bde309e67828fa7698926deb6db859eda18a8a7fcdccf924cb76 | 515 |

<!-- artifact-links:end -->
