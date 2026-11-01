# MIO 2021 Q4: partial study, two completed methods

Two completed independent broad212 accounts; 64 quarter sessions and 14 weekly decisions each. M53 is frozen at quarter start. Both named methods passed replay and standard report accounting gates. The overall study is incomplete: nominal_lp failed settling an IBM/Kyndryl mixed-deliverable contract and has no final totals.

**nominal_lp failed:** ValueError: unsettleable selected book: 2021-10-25:IBM:2021-12-17. No valid final profit or trade count is available for it. [Failure and scope proposal](../q4-partial-failure.json). The source result remains complete=false.

## Quarter end and later settlement

Quarter-end figures mark open options at the saved daily MIO marks. Settled cohort profit includes the later expiries of positions opened during this quarter. Dollar profit includes fees and excludes deposits. TWR adjusts for scheduled deposits; it is not dollar profit divided by total contributions.

- [w1_003_shock10_lp](w1_003_shock10_lp/summary.md) · [HTML](w1_003_shock10_lp/report.html) · [marked ledger](w1_003_shock10_lp/ledger.csv): quarter-end marked P&L **$-3,045.50**, TWR **-12.99%**, 6 open books; settled profit **$-7,937.50**, TWR **-45.85%**. Filled 19 books / 25 leg rows / 110 contracts; refused 27 books; fees $71.50.
- [w1_003_lp](w1_003_lp/summary.md) · [HTML](w1_003_lp/report.html) · [marked ledger](w1_003_lp/ledger.csv): quarter-end marked P&L **$-1,873.35**, TWR **-5.04%**, 2 open books; settled profit **$-8,239.85**, TWR **-47.84%**. Filled 4 books / 6 leg rows / 69 contracts; refused 19 books; fees $44.85.

Each account received $16,000.00. Accounts reset at each quarter start: do not compound these returns or combine the methods as one account. Every position is settled by its recorded expiry; the padded account calendar ends 2022-03-08.

## Execution detail

- [Original selected quantities and actual fills, one row per option leg](orders-and-fills.csv)
- [Filled books, including original leg lists](trades.csv)
- [Refused books and reasons](rejected-orders.csv)
- [Every marked account session, including holdings](daily-account.csv)
- [Result and report verification](mio-verification.json) · [report gates and artifact hashes](reports-verification.json) · [source and mirror index](artifact-index.json)

A book is a group of option legs. A leg row is one side/right/strike within a selected book; contracts are the sum of absolute executed leg quantities. The standard event report counts opening and closing events separately, so its fill count is twice the number of filled books. Its generic charts value open books at cost; use the marked ledger and quarter boundary for authoritative account values. Decision-stage skips are separate from fill-stage refused books.

For refused books, the standard ledger may show a reduced fill-stage quantity. The order-leg CSV retains the optimizer-selected quantity beside executed quantity. Fill dates are simulated next-session execution, not observed intraday timestamps.

## Protocol and limitations

The neural final fit uses 36 epochs. Pre-Q2 selected 46 of 96 run; pre-Q3 selected 36 of 86 run with patience 50 and maximum 150. Q4 reuses the pre-Q3 epoch choice and adds mature Q3 outcomes. Q3/Q4 outcomes do not select epochs. M53 uses only settled prior outcomes at each quarter start.

M53 had no separate formal HPO and was chosen among 67 saved methods using 2020–2023 scores. These 2021 results are retrospective development evidence, not an untouched holdout or a prospective profitability claim. No 2024+ price or outcome records entered model fitting or evaluation. Earlier 2019/Q1 coverage was truncated and is not corrected by this report.

The outer portfolio solver uses 900 seconds, one thread, relative gap 0.01 and absolute gap $50; inner book construction limits differ. American early assignment is unmodeled. Displayed-depth simulated fills do not establish broker execution. No new execution-stress scenarios were run.

This directory is a byte-preserving mirror of the generated standard reports plus a navigation note and direct saved-field extracts. The full immutable result, original attempts, models and exposure history remain under the recorded local evidence roots.
