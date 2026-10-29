# MIO limited36 available-size: nominal_lp

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

45 round trips over 53 sessions (53 decisions). Decisions ran from 2019-01-02 16:00 to 2019-12-30 16:00 (America/New_York); the data (prices, fills, cash flows) span 2019-01-02 08:00 to 2019-12-30 16:00. Before costs the strategy made +$989.00; fees were $729.95, so net was +$259.05 (costs were 0.738x gross). 13.3% of round trips won; the average win ($7,194.43) was larger than the average loss ($1,100.19). Best instrument 2019-12-09:HUM:2019-12-27 (+$16,330.50 over 1 trips); worst 2019-10-07:CME:2019-11-15 (-$4,154.15 over 1). Worst drawdown of trading P&L: $21,576.05 (121% of equity at the peak). External cash flows of +$35,000.00 (26 flows) are in the account balance but are not profit. 82 actions were refused: missing_fill_quote (54), insufficient_fill_size (22), repriced_shared_cap (6). 184 actions were skipped: no_eligible_forecast_expiry (183), insufficient_eligible_legs (1). Every decision and order is accounted for. No criteria were pre-registered, so there is no verdict (UNJUDGED).

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
| run | 7f5b107f734546f2d5577513bb4f635dad2e9b0feac2f25ecb2e625d38848393 |
| project | index_options |
| decision window | 2019-01-02 16:00 → 2019-12-30 16:00 (53 sessions) |
| data window | 2019-01-02 08:00 → 2019-12-30 16:00 (156 sessions) |
| time zone | America/New_York |
| instruments | 2019-01-02:BLK:2019-02-08, 2019-01-02:DIS:2019-02-15, 2019-01-02:RCL:2019-02-15, 2019-01-02:TGT:2019-02-15, 2019-01-07:B… |
| score unit | raw |
| currency | USD |
| events | 616 |
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
| net_pnl | $259.05 | — | 0 | — |
| gross_pnl | $989.00 | — | 0 | — |
| fees | $729.95 | — | 0 | — |
| cost_share | 0.738 | — | 0 | — |
| twr (at cost) | -42.2% | — | 0 | — |
| max_drawdown (at cost) | $21,576.05 | — | 0 | — |
| max_drawdown_pct (at cost) | 121% | — | 0 | — |
| max_drawdown_minutes (at cost) | 336,060 min | — | 0 | — |
| trades | 45 | — | 0 | — |
| hit_rate | 13.3% | 45 | 30 | — |
| avg_win | $7,194.43 | 45 | 30 | — |
| avg_loss | -$1,100.19 | 45 | 30 | — |
| payoff_ratio | 6.54 | 45 | 30 | — |
| profit_factor | 1.01 | 45 | 30 | — |
| trade_mean | $5.76 | 45 | 30 | — |
| trade_t | 0.00995 | 45 | 30 | — |
| trade_p | 0.496 | 45 | 30 | — |
| trade_es95 | -$3,569.83 | 45 | 30 | — |
| days | 84 | — | 0 | — |
| daily_mean_return (at cost) | 1.36% | 84 | 30 | — |
| daily_sharpe (at cost) | 0.0483 | 84 | 30 | — |
| daily_sharpe_ci_low (at cost) | -0.133 | 84 | 30 | — |
| daily_sharpe_ci_high (at cost) | 0.229 | 84 | 30 | — |
| psr (at cost) | 0.700 | 84 | 30 | — |
| dsr (at cost) | 0.700 | 84 | 30 | — |
| decisions | 53 | — | 0 | — |
| orders | 127 | — | 0 | — |
| fills | 90 | — | 0 | — |
| fill_ratio | 35.4% | — | 0 | — |
| refusal_rate (decisions) | 0% | — | 0 | — |
| skip_rate (decisions) | 0% | — | 0 | — |
| turnover_per_day (at cost) | 0.0661 | — | 0 | — |
| time_in_market | 95.3% | — | 0 | — |
| avg_exposure (at cost) | 0.359 | — | 0 | — |
| holding_median_minutes | 45,030 min | 45 | 30 | — |
| holding_p90_minutes | 65,190 min | 45 | 30 | — |

## Census

| item | count |
|---|---|
| decisions | 53 |
|   action enter | 52 |
|   action exit | 0 |
|   action hold | 1 |
|   action skip | 0 |
|   action refuse | 0 |
| orders | 127 |
| fills | 90 |
| refusals | 82 |
| skips | 184 |
|   orders filled | 45 |
|   orders rejected | 82 |
| fills with no order | 45 |

All accounted for.

## Top refusal / skip reasons

| kind | reason | count |
|---|---|---|
| skip | no_eligible_forecast_expiry | 183 |
| refusal | missing_fill_quote | 54 |
| refusal | insufficient_fill_size | 22 |
| refusal | repriced_shared_cap | 6 |
| skip | insufficient_eligible_legs | 1 |


**Optimizer:** 52 solves (ok/optimal 52).


**Day-by-day ledger (marked daily curve):** 252 rows in `ledger.csv`.


**Saved marked figures (authoritative):** 6 rows in `marked-figures.csv`.


**Exact-match gate: events against the saved result:** 7 rows in `gate-comparison.csv`.


**Filled books by ticker and DTE:** 22 rows in `ticker-dte.csv`.


**Fill-stage refusals by ticker, DTE and reason:** 41 rows in `refusals-ticker-dte.csv`.


**Run identity and reused coverage:** 18 rows in `run-identity.csv`.

## Provenance

| field | value | source |
|---|---|---|
| schema | dskit-eval-v1 | — |
| run_id | 7f5b107f734546f2d5577513bb4f635dad2e9b0feac2f25ecb2e625d38848393 | — |
| config_hash | c9d07ce7373d27ec402892a7ada41e1df5e3e2819f7d7f506ade266ec35a489c | — |
| code commit | 63949a8ad441263851e9a89fe85da0c7679da72f | — |
| code dirty | no | — |
| trials | 1 | — |
| python | 3.12.3 | RuntimeFingerprint of the interpreter that rendered the report |
| platform | Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39 | RuntimeFingerprint of the interpreter that rendered the report |
| packages | 178 | RuntimeFingerprint of the interpreter that rendered the report |
| wall time | 0.1640 s | report node: now minus config.json mtime (run start); excludes rendering |
| events | cashflow=26, decision=53, fill=90, order=127, refusal=82, run_end=1, run_start=1, skip=184, solve=52 | — |

<!-- artifact-links:begin -->
## Stress links (ADR-0280 fixed-selection replay, schema v4)

Read-only links to files under /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4. A file not yet written is pending: no hash or size is shown for it.

### Links

| id | key | path | status | sha256 | bytes | pinned |
|---|---|---|---|---|---|---|
| stress_report | — | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/report.md | present | c128afe1e81a9a24830a740dc22f90bb17093a13550581c71aa4441721799eaf | 11023 | match |
| stress_summary | — | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/summary.csv | present | 01891735bf80aa029ac0d7afa730e87f1dbdb2272b7149882c9815697b0c0f44 | 22536 | match |
| stress_pins | — | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/pins.json | present | 21b6da0f0a707df157633d75db1c8b1095677993b9821f3e1ecd940db69185a3 | 52972 | n/a |
| daybook | scenario=control,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/control/nominal_lp.md | present | 5a013f5e814d8527743b5937feeeb4e5bacaa8f40ff600dd4c9beb6b8c4c8f9b | 48091 | match |
| daybook | scenario=control,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/control/w1_003_lp.md | present | b433f661b1246d56f9d4e5f79988032e5211a16af248185571a419c5873ef91b | 35634 | match |
| daybook | scenario=control,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/control/w1_003_shock10_l… | present | ad470decb681fac3147e037410fa04cdc62c8cbe02e195d79526f30ef6ecef63 | 35417 | match |
| daybook | scenario=control,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/control/w1_003_component… | present | c1d01e298bde1611eedc0333d44b1257123b70c6af10b5c043f0f8673794c9ec | 35184 | match |
| daybook | scenario=adverse_price,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/adverse_price/nominal_lp… | present | a1b3d3f57b3add61897a169691d8c6ae4920203198798cc4ad0e5d9265e1bd98 | 46999 | match |
| daybook | scenario=adverse_price,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/adverse_price/w1_003_lp.… | present | 96d4512096c0e889dd3ad017b5d6f3875034d613308e5bd64ed51b71e0652830 | 35300 | match |
| daybook | scenario=adverse_price,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/adverse_price/w1_003_sho… | present | fd82ea1e0843c5d7aab7ad2456f0990611286ec31e2b09ab9f4e00f49d10c20d | 35424 | match |
| daybook | scenario=adverse_price,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/adverse_price/w1_003_com… | present | 3d35832f029def5c2d73fcdf8641e34c9b4cfa6d0e859b12f73b767664bce877 | 34973 | match |
| daybook | scenario=half_size,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/half_size/nominal_lp.md | present | 1b9b2d128bc5c71c04803f354ca5f9160fb0b98ffe55f4b6180c7378546ddf47 | 48282 | match |
| daybook | scenario=half_size,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/half_size/w1_003_lp.md | present | fce36fad7b6414d8cf174a7295d44f9e49af87d831de1daaa24af59fe1e8dc56 | 34597 | match |
| daybook | scenario=half_size,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/half_size/w1_003_shock10… | present | 6b9055edb89243d33fe84e7e82c91956452f858b809ba757e4a284fab8f98bd1 | 34970 | match |
| daybook | scenario=half_size,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/half_size/w1_003_compone… | present | ea4467baeb716d4c18158910fb22a5b384290605b93a670d4aab5f3fca323ddc | 34772 | match |
| daybook | scenario=expiry_down_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_down_15/nominal_l… | present | 13779bcb5e3860541f3c333c49ba9ecc8cb8b51af3f6fd8cc818c1ebf301cb29 | 46171 | match |
| daybook | scenario=expiry_down_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_down_15/w1_003_lp… | present | 75ff70cf31f0a038df5c1779772cf174b0dded3535879903cb26b0e9d7ec5c64 | 34595 | match |
| daybook | scenario=expiry_down_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_down_15/w1_003_sh… | present | e38f29a212200ed81c96d2df7d346152d6da7cdc1d30521baa7fc487aa7cbe98 | 34488 | match |
| daybook | scenario=expiry_down_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_down_15/w1_003_co… | present | 2f455821cc892e0d5d50ae5b41903d70875101ea06ad67caedb8416332c5b181 | 34682 | match |
| daybook | scenario=expiry_up_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_up_15/nominal_lp.… | present | 094d4ff55751600c7b20ecd4a218022ffc8b435140a09869615cb81eaac6aeed | 50563 | match |
| daybook | scenario=expiry_up_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_up_15/w1_003_lp.m… | present | 36c1a523b619c8a69e6c23991d14d9bea6c13c4c6fd97b7f15708a83ed8df3e3 | 35899 | match |
| daybook | scenario=expiry_up_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_up_15/w1_003_shoc… | present | f0289a6bd92ed8d38f2aea2feb2a322f434aa630cdeaf614286095195fdffda5 | 35860 | match |
| daybook | scenario=expiry_up_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/expiry_up_15/w1_003_comp… | present | a6140b95e5b09a069f7b08c43893180e972d827329f1dfb08fd07b31ba72b575 | 35903 | match |
| daybook | scenario=joint_down_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_down_15/nominal_lp… | present | 7414fbae2706869bf629d6641bc9ca606928ed8092721198271fddd9459ba532 | 46483 | match |
| daybook | scenario=joint_down_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_down_15/w1_003_lp.… | present | d7135d892ee6a97fb1728ed707898feefc02c0451b82db9cfc5750ec74a1c2ae | 33943 | match |
| daybook | scenario=joint_down_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_down_15/w1_003_sho… | present | 81e842bcfbc3ee6982a79069b4aaef48d019dbc82ed0b582e9014ff195ff9633 | 34140 | match |
| daybook | scenario=joint_down_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_down_15/w1_003_com… | present | 4b7eb16a75fc0fa2db211831c0b2b1faf45395e43366b5db02c241387dae9cde | 34237 | match |
| daybook | scenario=joint_up_15,method=nominal_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_up_15/nominal_lp.m… | present | ae8d2ac1de482f600e02f5f3b9b2b5556067df9a4135194b9e4155f524560b97 | 48889 | match |
| daybook | scenario=joint_up_15,method=w1_003_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_up_15/w1_003_lp.md | present | 0824f454c56eac2d5fe47b49fd95b5c22ae76640107c6a8360b546c518ea85b6 | 34607 | match |
| daybook | scenario=joint_up_15,method=w1_003_shock10_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_up_15/w1_003_shock… | present | c777919d164da25d66a5b286388e62205930b4725a543c399e031041771465e7 | 34966 | match |
| daybook | scenario=joint_up_15,method=w1_003_component25_lp | /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4/limited36/joint_up_15/w1_003_compo… | present | 988e4c91710ac340357c9f2742e4f8ab99aa4772501b9f6cd5d7b4b54410cde6 | 34627 | match |

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
