# MIO 2021 Q4: w1_003_shock10_lp — complete coverage, quarter-start M53

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
> - Each quarter is an independent $10,000 starting account plus $1,000 every 14 days. Do not compound returns across these independent accounts.
> - The report includes settlement after quarter end. See the quarter-end versus settled-cohort table for the requested quarter boundary.
> - Q4 uses the owner-approved public-notice spinoff eligibility policy for all three methods: IBM/Kyndryl and Realty Income/Orion crossings excluded after dated announcements until verified standard contract resumption. Q2/Q3 retain original eligibility, including unresolved applicability comparisons for MRK/GE. These quarters are not a single unchanged-eligibility experiment.
> - Prior failed and partial Q4 attempts are retained with their exposure and numeric results; this variant was approved after that exposure. It is retrospective development.

## What happened

20 round trips over 14 sessions (14 decisions). Decisions ran from 2021-10-01 16:00 to 2021-12-27 16:00 (America/New_York); the data (prices, fills, cash flows) span 2021-10-01 08:00 to 2022-02-18 16:00. Before costs the strategy lost -$8,025.00; fees were $108.55, so net was -$8,133.55. 5.00% of round trips won; the average win ($698.50) was larger than the average loss ($464.84). Best instrument 2021-10-04:UUUU:2021-11-19 (+$698.50 over 1 trips); worst 2021-12-06:ENPH:2022-01-21 (-$2,120.65 over 1). Worst drawdown of trading P&L: $8,133.55 (81.3% of equity at the peak). External cash flows of +$16,000.00 (7 flows) are in the account balance but are not profit. 25 actions were refused: repriced_shared_cap (11), missing_fill_quote (10), insufficient_fill_size (4). 7,915 actions were skipped: insufficient_eligible_legs (7,910), no_eligible_forecast_expiry (5). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | c0eabe3e261ec35f318f1cffc56ac3057937164f5ebf8e5bd6527cd4971973b0 |
| project | index_options |
| decision window | 2021-10-01 16:00 → 2021-12-27 16:00 (14 sessions) |
| data window | 2021-10-01 08:00 → 2022-02-18 16:00 (39 sessions) |
| time zone | America/New_York |
| instruments | 2021-10-01:JPM:2021-10-08, 2021-10-01:MGM:2021-11-19, 2021-10-04:AVAV:2021-11-19, 2021-10-04:BB:2021-10-15, 2021-10-04:J… |
| score unit | raw |
| currency | USD |
| events | 8,062 |
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
> - Each quarter is an independent $10,000 starting account plus $1,000 every 14 days. Do not compound returns across these independent accounts.
> - The report includes settlement after quarter end. See the quarter-end versus settled-cohort table for the requested quarter boundary.
> - Q4 uses the owner-approved public-notice spinoff eligibility policy for all three methods: IBM/Kyndryl and Realty Income/Orion crossings excluded after dated announcements until verified standard contract resumption. Q2/Q3 retain original eligibility, including unresolved applicability comparisons for MRK/GE. These quarters are not a single unchanged-eligibility experiment.
> - Prior failed and partial Q4 attempts are retained with their exposure and numeric results; this variant was approved after that exposure. It is retrospective development.

| name | value | n | min_n | flag |
|---|---|---|---|---|
| net_pnl | -$8,133.55 | — | 0 | — |
| gross_pnl | -$8,025.00 | — | 0 | — |
| fees | $108.55 | — | 0 | — |
| cost_share | 0.0135 | — | 0 | — |
| twr (at cost) | -52.6% | — | 0 | — |
| max_drawdown (at cost) | $8,133.55 | — | 0 | — |
| max_drawdown_pct (at cost) | 81.3% | — | 0 | — |
| max_drawdown_minutes (at cost) | 202,140 min | — | 0 | — |
| trades | 20 | — | 0 | — |
| hit_rate | 5.00% | 20 | 30 | insufficient n |
| avg_win | $698.50 | 20 | 30 | insufficient n |
| avg_loss | -$464.84 | 20 | 30 | insufficient n |
| payoff_ratio | 1.50 | 20 | 30 | insufficient n |
| profit_factor | 0.0791 | 20 | 30 | insufficient n |
| trade_mean | -$406.68 | 20 | 30 | insufficient n |
| trade_t | -2.60 | 20 | 30 | insufficient n |
| trade_p | 0.991 | 20 | 30 | insufficient n |
| trade_es95 | -$2,120.65 | 20 | 30 | insufficient n |
| days | 23 | — | 0 | — |
| daily_mean_return (at cost) | -2.94% | 23 | 30 | insufficient n |
| daily_sharpe (at cost) | -0.432 | 23 | 30 | insufficient n |
| daily_sharpe_ci_low (at cost) | -0.645 | 23 | 30 | insufficient n |
| daily_sharpe_ci_high (at cost) | -0.219 | 23 | 30 | insufficient n |
| psr (at cost) | 0.0000352 | 23 | 30 | insufficient n |
| dsr (at cost) | 0.0000352 | 23 | 30 | insufficient n |
| decisions | 14 | — | 0 | — |
| orders | 45 | — | 0 | — |
| fills | 40 | — | 0 | — |
| fill_ratio | 44.4% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 0% | — | 0 | — |
| turnover_per_day (at cost) | 0.0430 | — | 0 | — |
| time_in_market | 92.6% | — | 0 | — |
| avg_exposure (at cost) | 0.281 | — | 0 | — |
| holding_median_minutes | 60,150 min | 20 | 30 | insufficient n |
| holding_p90_minutes | 76,332 min | 20 | 30 | insufficient n |

## Census

| item | count |
|---|---|
| decisions | 14 |
|   action enter | 14 |
|   action exit | 0 |
|   action hold | 0 |
|   action skip | 0 |
|   action refuse | 0 |
| orders | 45 |
| fills | 40 |
| refusals | 25 |
| skips | 7,915 |
|   orders filled | 20 |
|   orders rejected | 25 |
| fills with no order | 20 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | insufficient_eligible_legs | 7,910 |
| refusal | repriced_shared_cap | 11 |
| refusal | missing_fill_quote | 10 |
| skip | no_eligible_forecast_expiry | 5 |
| refusal | insufficient_fill_size | 4 |


**Optimizer:** 14 solves (ok/optimal 14).


**Day-by-day ledger (marked daily curve):** 109 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 19 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 23 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 10 rows in `run-identity.csv`.


**Quarter-end marked account and later settled cohort:** 25 rows in `quarter-boundary.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | c0eabe3e261ec35f318f1cffc56ac3057937164f5ebf8e5bd6527cd4971973b0 | — |
| config_hash | e3b4ddc4ffb878d42fcffd9d6ce5b4232e6710a108fcdf4c1ca6339a6eddd9d1 | — |
| code commit | — | — |
| code dirty | — | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 0.9330 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=7, decision=14, fill=40, order=45, refusal=25, run_end=1, run_start=1, skip=7915, solve=14 | — |

<!-- artifact-links:begin -->
## Attested Q4 source files

Read-only links to files under /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-eligibility-v2/mio-2021q4-eligibility-v2-walkforward-frozen-m53-development-2021-12-31-76d6c632. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes |
|---|---|---|---|---|---|
| result | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-eligibility-v2/mio-2021q4-eligi… | present | 5c40cc22f0bfd0b291caa3339912df55e134787559d49b09ad44ee61b6fbf649 | 14662260 |
| receipt | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-eligibility-v2/mio-2021q4-eligi… | present | 8511774c926bece1893054bce0542684a3747d02696f737fc8a359de89482170 | 533 |
| resolved | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-eligibility-v2/mio-2021q4-eligi… | present | b2c5b738bcb7e49f512368e787c5cea98ff152288e031809195a0658a133b5df | 308 |
| run_report | — | /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/runs/mio-q4-eligibility-v2/mio-2021q4-eligi… | present | 9ed1bc060285121022d70683c53eac9a514643989522d45cb079e38d9d17f9ea | 520 |

<!-- artifact-links:end -->
