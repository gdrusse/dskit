# MIO 2021 Q1 amended replay: w1_003_lp

> **Caveats**
>
> - Simulated end-of-day fills against displayed depth: 2021 Q1 amended replay evidence, not broker liquidity or proof of profitability.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Optimizer gap is relative; the run document's MIO solve accepts solutions by an absolute gap limit (mip_abs_gap, USD) with mip_rel_gap 0, so a large relative gap beside 'optimal' is not a failure.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.
> - The first Q1 attempt failed after four weeks on GME numerical integration roundoff at tolerance 1e-8. This is the reviewed amended_replay at 1e-6; the original failed receipt is preserved in the shared registry.
> - Q1 execution/expiry stress scenarios were not run. The 2019 stress results do not apply to these Q1 trades.
> - LLY and SMCI have no admitted Q1 forecast; both remain in the 212-name universe and their absence is visible. COHR had no Q1 validation rows in neural scoring.

## What happened

6 round trips over 13 sessions (13 decisions). Decisions ran from 2021-01-04 16:00 to 2021-03-29 16:00 (America/New_York); the data (prices, fills, cash flows) span 2021-01-04 08:00 to 2021-03-29 16:00. Before costs the strategy lost -$177.00; fees were $30.55, so net was -$207.55. 33.3% of round trips won; the average win ($660.50) was larger than the average loss ($382.14). Best instrument 2021-02-01:AMC:2021-03-19 (+$1,284.20 over 1 trips); worst 2021-03-15:AMC:2021-03-26 (-$1,430.60 over 1). Worst drawdown of trading P&L: $1,426.20 (8.79% of equity at the peak). External cash flows of +$16,000.00 (7 flows) are in the account balance but are not profit. 14 actions were refused: missing_fill_quote (7), insufficient_fill_size (5), repriced_shared_cap (2). 288 actions were skipped: no_eligible_forecast_expiry (281), insufficient_eligible_legs (7). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | 18cf40245b38c15d6a09630c6a56fefc3a8c627f62f0ddb3165eea16ae959067 |
| project | index_options |
| decision window | 2021-01-04 16:00 → 2021-03-29 16:00 (13 sessions) |
| data window | 2021-01-04 08:00 → 2021-03-29 16:00 (27 sessions) |
| time zone | America/New_York |
| instruments | 2021-01-04:AMC:2021-02-19, 2021-01-04:RIOT:2021-02-19, 2021-01-11:AMC:2021-02-19, 2021-01-11:AMC:2021-02-26, 2021-01-19:… |
| score unit | raw |
| currency | USD |
| events | 368 |
| run status | ok |
| bar stamps | ts_ms = the instant a mark was observed (UTC ms) |

**Verdict: UNJUDGED**

## Statistics

> **Caveats**
>
> - Simulated end-of-day fills against displayed depth: 2021 Q1 amended replay evidence, not broker liquidity or proof of profitability.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Optimizer gap is relative; the run document's MIO solve accepts solutions by an absolute gap limit (mip_abs_gap, USD) with mip_rel_gap 0, so a large relative gap beside 'optimal' is not a failure.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.
> - The first Q1 attempt failed after four weeks on GME numerical integration roundoff at tolerance 1e-8. This is the reviewed amended_replay at 1e-6; the original failed receipt is preserved in the shared registry.
> - Q1 execution/expiry stress scenarios were not run. The 2019 stress results do not apply to these Q1 trades.
> - LLY and SMCI have no admitted Q1 forecast; both remain in the 212-name universe and their absence is visible. COHR had no Q1 validation rows in neural scoring.

| name | value | n | min_n | flag |
|---|---|---|---|---|
| net_pnl | -$207.55 | — | 0 | — |
| gross_pnl | -$177.00 | — | 0 | — |
| fees | $30.55 | — | 0 | — |
| cost_share | 0.173 | — | 0 | — |
| twr (at cost) | -1.40% | — | 0 | — |
| max_drawdown (at cost) | $1,426.20 | — | 0 | — |
| max_drawdown_pct (at cost) | 8.79% | — | 0 | — |
| max_drawdown_minutes (at cost) | 61,950 min | — | 0 | — |
| trades | 6 | — | 0 | — |
| hit_rate | 33.3% | 6 | 30 | insufficient n |
| avg_win | $660.50 | 6 | 30 | insufficient n |
| avg_loss | -$382.14 | 6 | 30 | insufficient n |
| payoff_ratio | 1.73 | 6 | 30 | insufficient n |
| profit_factor | 0.864 | 6 | 30 | insufficient n |
| trade_mean | -$34.59 | 6 | 30 | insufficient n |
| trade_t | -0.0986 | 6 | 30 | insufficient n |
| trade_p | 0.537 | 6 | 30 | insufficient n |
| trade_es95 | -$1,430.60 | 6 | 30 | insufficient n |
| days | 13 | — | 0 | — |
| daily_mean_return (at cost) | -0.0518% | 13 | 30 | insufficient n |
| daily_sharpe (at cost) | -0.0149 | 13 | 30 | insufficient n |
| daily_sharpe_ci_low (at cost) | -0.580 | 13 | 30 | insufficient n |
| daily_sharpe_ci_high (at cost) | 0.550 | 13 | 30 | insufficient n |
| psr (at cost) | 0.479 | 13 | 30 | insufficient n |
| dsr (at cost) | 0.479 | 13 | 30 | insufficient n |
| decisions | 13 | — | 0 | — |
| orders | 20 | — | 0 | — |
| fills | 12 | — | 0 | — |
| fill_ratio | 30.0% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 0% | — | 0 | — |
| turnover_per_day (at cost) | 0.00640 | — | 0 | — |
| time_in_market | 64.3% | — | 0 | — |
| avg_exposure (at cost) | 0.0592 | — | 0 | — |
| holding_median_minutes | 49,290 min | 6 | 30 | insufficient n |
| holding_p90_minutes | 65,130 min | 6 | 30 | insufficient n |

## Census

| item | count |
|---|---|
| decisions | 13 |
|   action enter | 12 |
|   action exit | 0 |
|   action hold | 1 |
|   action skip | 0 |
|   action refuse | 0 |
| orders | 20 |
| fills | 12 |
| refusals | 14 |
| skips | 288 |
|   orders filled | 6 |
|   orders rejected | 14 |
| fills with no order | 6 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | no_eligible_forecast_expiry | 281 |
| refusal | missing_fill_quote | 7 |
| skip | insufficient_eligible_legs | 7 |
| refusal | insufficient_fill_size | 5 |
| refusal | repriced_shared_cap | 2 |


**Optimizer:** 12 solves (ok/optimal 12).


**Day-by-day ledger (marked daily curve):** 61 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 5 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 12 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 20 rows in `run-identity.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | 18cf40245b38c15d6a09630c6a56fefc3a8c627f62f0ddb3165eea16ae959067 | — |
| config_hash | 0d94aa6c806c965fe8ce349855c49e00cb6780443715fd9fe7a35304640c288b | — |
| code commit | c0e5bc6df820984dd6947e7a9e62a8dbf1cbc505 | — |
| code dirty | no | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 0.7250 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=7, decision=13, fill=12, order=20, refusal=14, run_end=1, run_start=1, skip=288, solve=12 | — |

<!-- artifact-links:begin -->
## Exact Q1 saved-field extracts and attested result

Read-only links to files under /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes | pinned |
|---|---|---|---|---|---|---|
| result_json | — | /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports/result.json | present | ce39e2062c378d6576af97f673f795df04b724b4f696729c677a43cdd94399ac | 19750290 | match |
| artifact-index_json | — | /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports/artifact-index.json | present | 8c86b718337975ff05c05bee20b385c987a5cbde18e02b595296f67e5288d364 | 1923 | n/a |
| trades_csv | — | /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports/trades.csv | present | fbd4709da08ea6654b26e6b3b27199aed54079e7861ad501910f6fc43b26b0ce | 22686 | match |
| rejected-orders_csv | — | /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports/rejected-orders.csv | present | 65f4ea8f111bdc6f3d8885f7f1eb14b40de7ed19952f8cde8a8588b7319bd8d9 | 58677 | match |
| orders-and-fills_csv | — | /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports/orders-and-fills.csv | present | 7c57ff3c5d95964b9ee2cf9477ac080c65913c211348824c6818235b8df8fd76 | 30945 | match |
| daily-account_csv | — | /home/russell/data/index_options/mio-2021q1-top3-20261029/mio/exports/daily-account.csv | present | 049ddcdd7e40ae9ce811c866c18419f13fc0f3289c7f4c5e0c4b7273f444b5ea | 37365 | match |

<!-- artifact-links:end -->
