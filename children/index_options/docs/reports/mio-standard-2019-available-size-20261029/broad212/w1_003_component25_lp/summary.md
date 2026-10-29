# MIO broad212 available-size: w1_003_component25_lp

> **Caveats**
>
> - Simulated end-of-day fills against displayed depth: 2019 development evidence, not broker liquidity and not proof of profitability.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Optimizer gap is relative; the run document's MIO solve accepts solutions by an absolute gap limit (mip_abs_gap, USD) with mip_rel_gap 0, so a large relative gap beside 'optimal' is not a failure.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.

## What happened

42 round trips over 53 sessions (53 decisions). Decisions ran from 2019-01-02 16:00 to 2019-12-30 16:00 (America/New_York); the data (prices, fills, cash flows) span 2019-01-02 08:00 to 2019-12-30 16:00. Before costs the strategy made +$12,445.00; fees were $851.50, so net was +$11,593.50 (costs were 0.0684x gross). 28.6% of round trips won; the average win ($5,216.75) was larger than the average loss ($1,700.25). Best instrument 2019-01-22:W:2019-03-15 (+$39,853.50 over 1 trips); worst 2019-04-08:AG:2019-05-24 (-$11,304.25 over 1). Worst drawdown of trading P&L: $34,878.50 (55.6% of equity at the peak). External cash flows of +$35,000.00 (26 flows) are in the account balance but are not profit. 64 actions were refused: missing_fill_quote (28), insufficient_fill_size (18), repriced_shared_cap (17) and 1 more. 619 actions were skipped: no_eligible_forecast_expiry (385), insufficient_eligible_legs (234). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | 74126b30d15154250bf464ba1a16b3ecadc2cae7786aaa5885c1e2816ab7445e |
| project | index_options |
| decision window | 2019-01-02 16:00 → 2019-12-30 16:00 (53 sessions) |
| data window | 2019-01-02 08:00 → 2019-12-30 16:00 (145 sessions) |
| time zone | America/New_York |
| instruments | 2019-01-02:CVNA:2019-02-15, 2019-01-07:RCL:2019-02-15, 2019-01-07:RIOT:2019-01-25, 2019-01-14:TSLA:2019-03-01, 2019-01-1… |
| score unit | raw |
| currency | USD |
| events | 1,001 |
| run status | ok |
| bar stamps | ts_ms = the instant a mark was observed (UTC ms) |

**Verdict: UNJUDGED**

## Statistics

> **Caveats**
>
> - Simulated end-of-day fills against displayed depth: 2019 development evidence, not broker liquidity and not proof of profitability.
> - Statistics below come from the event book, which values open books at cost between fills (TWR, drawdown, Sharpe, exposure). The MIO result's marked figures are authoritative: see 'Saved marked figures' and the day-by-day ledger.
> - Each book is one unit at its net premium; a credit book appears as a buy at a negative price. Totals are exact; direction and notional statistics are not meaningful per book.
> - refusal_rate and skip_rate count decisions, not orders; order refusals are in the refusal tables.
> - Optimizer gap is relative; the run document's MIO solve accepts solutions by an absolute gap limit (mip_abs_gap, USD) with mip_rel_gap 0, so a large relative gap beside 'optimal' is not a failure.
> - Plain-language terms: children/index_options/docs/explanations/mio-glossary.md.
> - Intraday timestamps are report ordering slots; the source records dates and simulated end-of-day quotes, not observed intraday execution times.
> - Ledger selected_contracts can include reduced quantities for refused orders; use the linked report index orders-and-fills.csv selected_quantity for exact optimizer demand.

| name | value | n | min_n | flag |
|---|---|---|---|---|
| net_pnl | $11,593.50 | — | 0 | — |
| gross_pnl | $12,445.00 | — | 0 | — |
| fees | $851.50 | — | 0 | — |
| cost_share | 0.0684 | — | 0 | — |
| twr (at cost) | 106% | — | 0 | — |
| max_drawdown (at cost) | $34,878.50 | — | 0 | — |
| max_drawdown_pct (at cost) | 55.6% | — | 0 | — |
| max_drawdown_minutes (at cost) | 336,060 min | — | 0 | — |
| trades | 42 | — | 0 | — |
| hit_rate | 28.6% | 42 | 30 | — |
| avg_win | $5,216.75 | 42 | 30 | — |
| avg_loss | -$1,700.25 | 42 | 30 | — |
| payoff_ratio | 3.07 | 42 | 30 | — |
| profit_factor | 1.23 | 42 | 30 | — |
| trade_mean | $276.04 | 42 | 30 | — |
| trade_t | 0.260 | 42 | 30 | — |
| trade_p | 0.398 | 42 | 30 | — |
| trade_es95 | -$6,754.85 | 42 | 30 | — |
| days | 71 | — | 0 | — |
| daily_mean_return (at cost) | 3.49% | 71 | 30 | — |
| daily_sharpe (at cost) | 0.0939 | 71 | 30 | — |
| daily_sharpe_ci_low (at cost) | -0.0517 | 71 | 30 | — |
| daily_sharpe_ci_high (at cost) | 0.240 | 71 | 30 | — |
| psr (at cost) | 0.897 | 71 | 30 | — |
| dsr (at cost) | 0.897 | 71 | 30 | — |
| decisions | 53 | — | 0 | — |
| orders | 106 | — | 0 | — |
| fills | 84 | — | 0 | — |
| fill_ratio | 39.6% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 9.43% | — | 0 | — |
| turnover_per_day (at cost) | 0.0559 | — | 0 | — |
| time_in_market | 94.4% | — | 0 | — |
| avg_exposure (at cost) | 0.298 | — | 0 | — |
| holding_median_minutes | 63,720 min | 42 | 30 | — |
| holding_p90_minutes | 75,324 min | 42 | 30 | — |

## Census

| item | count |
|---|---|
| decisions | 53 |
|   action enter | 47 |
|   action exit | 0 |
|   action hold | 1 |
|   action skip | 5 |
|   action refuse | 0 |
| orders | 106 |
| fills | 84 |
| refusals | 64 |
| skips | 619 |
|   orders filled | 42 |
|   orders rejected | 64 |
| fills with no order | 42 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | no_eligible_forecast_expiry | 385 |
| skip | insufficient_eligible_legs | 234 |
| refusal | missing_fill_quote | 28 |
| refusal | insufficient_fill_size | 18 |
| refusal | repriced_shared_cap | 17 |
| refusal | fill_basis_or_session | 1 |


**Optimizer:** 47 solves (ok/optimal 47).


**Day-by-day ledger (marked daily curve):** 252 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 37 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 55 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 18 rows in `run-identity.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | 74126b30d15154250bf464ba1a16b3ecadc2cae7786aaa5885c1e2816ab7445e | — |
| config_hash | f2244361957e656078ecdb400f194c3fe0d0960113ddbbd9f1e1547c2fb3ea7a | — |
| code commit | 63949a8ad441263851e9a89fe85da0c7679da72f | — |
| code dirty | no | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 2.860 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=26, decision=53, fill=84, order=106, refusal=64, run_end=1, run_start=1, skip=619, solve=47 | — |

<!-- artifact-links:begin -->
## Stress links (ADR-0280 fixed-selection replay, schema v4)

Read-only links to files under /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes | pinned |
|---|---|---|---|---|---|---|
| stress_report | — | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/report.md | present | c128afe1e81a9a24830a740dc22f90bb17093a13550581c71aa4441721799eaf | 11023 | match |
| stress_summary | — | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/summary.csv | present | 01891735bf80aa029ac0d7afa730e87f1dbdb2272b7149882c9815697b0c0f44 | 22536 | match |
| stress_pins | — | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/pins.json | present | 21b6da0f0a707df157633d75db1c8b1095677993b9821f3e1ecd940db69185a3 | 52972 | n/a |
| daybook | scenario=control,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/control/nominal_lp.md | present | 7ffc56422d27fb5590babcd729f4d3c6015865a60e5ad05bcc6e7f448005db99 | 114858 | match |
| daybook | scenario=control,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/control/w1_003_lp.md | present | 109b2bda1cf6fef1ed5dbfdb77e177f78f00802c5bb3031350f20425d87d41f7 | 70742 | match |
| daybook | scenario=control,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/control/w1_003_shock10_lp… | present | f00d6a718a57e9519a4f91742397a082b11d71c5f7de5e10b0553a1ede8bbe37 | 83080 | match |
| daybook | scenario=control,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/control/w1_003_component2… | present | 304c05251e213e5eeaf69aa5c856bb91c72ceafbfd9b22ea83c215afef3423fd | 73308 | match |
| daybook | scenario=adverse_price,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/adverse_price/nominal_lp.… | present | 8508298f97ca9800637de1d546524015d95021b49be90eb6a054c5fb2bc630b1 | 114872 | match |
| daybook | scenario=adverse_price,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/adverse_price/w1_003_lp.m… | present | b32044e6be4014f614c1194888fcf6b507d4058add7316ffee4d296d9ea01e8f | 70421 | match |
| daybook | scenario=adverse_price,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/adverse_price/w1_003_shoc… | present | efbcafbe1668841252827e38de7543610b38e1de2da913b3a4a6d7a425b0f7b3 | 82224 | match |
| daybook | scenario=adverse_price,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/adverse_price/w1_003_comp… | present | b1615d80f07a23da0b4e55b6501d654e73bb95b26b0c622255bee1963de3b858 | 67692 | match |
| daybook | scenario=half_size,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/half_size/nominal_lp.md | present | 7ea3967491c24aa6101d3a7a507d2b32774e5ca335bab6ec0c54e039b12de9ab | 111278 | match |
| daybook | scenario=half_size,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/half_size/w1_003_lp.md | present | 854ce424abcaeabd753b5f404a3839daf074b6795327c86d7cb76b14996d17b1 | 68508 | match |
| daybook | scenario=half_size,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/half_size/w1_003_shock10_… | present | df799da41c446288912b1424d504373d151134a52e98022515605aeed2e3380a | 81655 | match |
| daybook | scenario=half_size,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/half_size/w1_003_componen… | present | 2dfca09ddb6210287f0ea2837cb44373b07cf7d45e4bbe981311a74a12099389 | 71795 | match |
| daybook | scenario=expiry_down_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_down_15/nominal_lp… | present | 15cbea61025248669074c1bad3332c7819f5f21275f4d97e1497007224965099 | 101806 | match |
| daybook | scenario=expiry_down_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_down_15/w1_003_lp.… | present | 8107fbf41ef96242c4aa7212f3575527543bd33d005d60d25e32fe04241f13ca | 66953 | match |
| daybook | scenario=expiry_down_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_down_15/w1_003_sho… | present | f15fcc0f9ef534abfe122dc3576e0e56273577365e6476bbd6c575c1a91985a0 | 85104 | match |
| daybook | scenario=expiry_down_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_down_15/w1_003_com… | present | 31cc83cf69b26aaf46b20f4ff217628fcdf0ca05f74695eefd681c85bb1419c1 | 72155 | match |
| daybook | scenario=expiry_up_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_up_15/nominal_lp.m… | present | 66ad2d502d028bb501fb91a4407231bbd5728ef8c410efed6b7f6a79a4bc1350 | 117490 | match |
| daybook | scenario=expiry_up_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_up_15/w1_003_lp.md | present | 08e86c525c45028d2f125be75eaaada5d43c2823e054d76f91f61f8595ca5b54 | 73938 | match |
| daybook | scenario=expiry_up_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_up_15/w1_003_shock… | present | fedbfc7fafad5b15230d12fa85d22563449ecb3a775374cc377bb35d7b645a3e | 85105 | match |
| daybook | scenario=expiry_up_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/expiry_up_15/w1_003_compo… | present | 04712515c8fd2f203d532811f78dba3ee136876321ea5f36ca540a90da97d9f9 | 74623 | match |
| daybook | scenario=joint_down_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_down_15/nominal_lp.… | present | 0eb6ea033f6192870a47f60145765b0d29c8f1dc3d820a87f4d9c465be5a99b8 | 104295 | match |
| daybook | scenario=joint_down_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_down_15/w1_003_lp.m… | present | e619452de4a72c066f640909c83d84ab9e9c7382c7dcdbd3a2e6aca84a839421 | 67400 | match |
| daybook | scenario=joint_down_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_down_15/w1_003_shoc… | present | dc777f9846131c9e545f8093413ec0d46dcd6f89b55193efd7b4f2821b67799b | 82259 | match |
| daybook | scenario=joint_down_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_down_15/w1_003_comp… | present | 8104af6202f884e9573a010ac9280415089321911c387e53594e4f3d48f4b1ad | 69877 | match |
| daybook | scenario=joint_up_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_up_15/nominal_lp.md | present | fb5b243b5fcfe852aa9d1849665fff6c9415bcdafa6e341d8b0a71e351bc45c9 | 113229 | match |
| daybook | scenario=joint_up_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_up_15/w1_003_lp.md | present | e187dbefb514b9a73fc1ed70f404ff6c962622b45da2c83f92a942725100c9aa | 69746 | match |
| daybook | scenario=joint_up_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_up_15/w1_003_shock1… | present | 435abd126915456e5ff7e2126a9a3199e1a319667e41af4fe570f4c7bd739876 | 82251 | match |
| daybook | scenario=joint_up_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/broad212/joint_up_15/w1_003_compon… | present | 70d5343d0ed4569c45b838c5fc06783a66f3ca5e4935c36e2d2d15b00807c44a | 69397 | match |

### Checks

| check | status | found | expected | cases | pinned |
|---|---|---|---|---|---|
| gate0_this_cohort_4_of_8_controls | complete | 4 | 4 | control | match |
| grid | complete | 28 | 28 | control,adverse_price,half_size,expiry_down_15,expiry_up_15,joint_down_15,joint_up_15 | match |

### Cells

| case | unit | status |
|---|---|---|
| control | nominal_lp | ok |
| control | w1_003_lp | ok |
| control | w1_003_shock10_lp | ok |
| control | w1_003_component25_lp | ok |
| adverse_price | nominal_lp | ok |
| adverse_price | w1_003_lp | ok |
| adverse_price | w1_003_shock10_lp | ok |
| adverse_price | w1_003_component25_lp | ok |
| half_size | nominal_lp | ok |
| half_size | w1_003_lp | ok |
| half_size | w1_003_shock10_lp | ok |
| half_size | w1_003_component25_lp | ok |
| expiry_down_15 | nominal_lp | ok |
| expiry_down_15 | w1_003_lp | ok |
| expiry_down_15 | w1_003_shock10_lp | ok |
| expiry_down_15 | w1_003_component25_lp | ok |
| expiry_up_15 | nominal_lp | ok |
| expiry_up_15 | w1_003_lp | ok |
| expiry_up_15 | w1_003_shock10_lp | ok |
| expiry_up_15 | w1_003_component25_lp | ok |
| joint_down_15 | nominal_lp | ok |
| joint_down_15 | w1_003_lp | ok |
| joint_down_15 | w1_003_shock10_lp | ok |
| joint_down_15 | w1_003_component25_lp | ok |
| joint_up_15 | nominal_lp | ok |
| joint_up_15 | w1_003_lp | ok |
| joint_up_15 | w1_003_shock10_lp | ok |
| joint_up_15 | w1_003_component25_lp | ok |

<!-- artifact-links:end -->
