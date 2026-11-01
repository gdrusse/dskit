# MIO 2021 Q2: complete-quarter walk-forward evaluation

Three independent broad212 accounts; 63 quarter sessions and 14 weekly decisions each. M53 is frozen at quarter start. All three attested replays and standard report accounting gates passed.

## Quarter end and later settlement

Quarter-end figures mark open options at the saved daily MIO marks. Settled cohort profit includes the later expiries of positions opened during this quarter. Dollar profit includes fees and excludes deposits. TWR adjusts for scheduled deposits; it is not dollar profit divided by total contributions.

- [w1_003_shock10_lp](w1_003_shock10_lp/summary.md) · [HTML](w1_003_shock10_lp/report.html) · [marked ledger](w1_003_shock10_lp/ledger.csv): quarter-end marked P&L **$6,515.85**, TWR **27.22%**, 10 open books; settled profit **$1,853.85**, TWR **0.88%**. Filled 22 books / 52 leg rows / 291 contracts; refused 13 books; fees $189.15.
- [nominal_lp](nominal_lp/summary.md) · [HTML](nominal_lp/report.html) · [marked ledger](nominal_lp/ledger.csv): quarter-end marked P&L **$-8,964.55**, TWR **-63.60%**, 4 open books; settled profit **$-9,242.55**, TWR **-65.04%**. Filled 11 books / 13 leg rows / 207 contracts; refused 15 books; fees $134.55.
- [w1_003_lp](w1_003_lp/summary.md) · [HTML](w1_003_lp/report.html) · [marked ledger](w1_003_lp/ledger.csv): quarter-end marked P&L **$14,381.35**, TWR **79.35%**, 6 open books; settled profit **$-3,473.15**, TWR **-26.05%**. Filled 12 books / 24 leg rows / 191 contracts; refused 10 books; fees $124.15.

Each account received $16,000.00. Accounts reset at each quarter start: do not compound these returns or combine the methods as one account. Every position is settled by its recorded expiry; the padded account calendar ends 2021-09-03.

## Execution detail

- [Original selected quantities and actual fills, one row per option leg](orders-and-fills.csv)
- [Filled books, including original leg lists](trades.csv)
- [Refused books and reasons](rejected-orders.csv)
- [Every marked account session, including holdings](daily-account.csv)
- [Result and report verification](mio-verification.json) · [report gates and artifact hashes](reports-verification.json) · [source and mirror index](artifact-index.json)

A book is a group of option legs. A leg row is one side/right/strike within a selected book; contracts are the sum of absolute executed leg quantities. The standard event report counts opening and closing events separately, so its fill count is twice the number of filled books. Its generic charts value open books at cost; use the marked ledger and quarter boundary for authoritative account values. Decision-stage skips are separate from fill-stage refused books.

For refused books, the standard ledger may show a reduced fill-stage quantity. The order-leg CSV retains the optimizer-selected quantity beside executed quantity. Fill dates are simulated next-session execution, not observed intraday timestamps.

## Protocol and limitations

The neural final fit uses 46 epochs. Pre-Q2 selected 46 of 96 run; pre-Q3 selected 36 of 86 run with patience 50 and maximum 150. Q4 reuses the pre-Q3 epoch choice and adds mature Q3 outcomes. Q3/Q4 outcomes do not select epochs. M53 uses only settled prior outcomes at each quarter start.

M53 had no separate formal HPO and was chosen among 67 saved methods using 2020–2023 scores. These 2021 results are retrospective development evidence, not an untouched holdout or a prospective profitability claim. No 2024+ price or outcome records entered model fitting or evaluation. Earlier 2019/Q1 coverage was truncated and is not corrected by this report.

The outer portfolio solver uses 900 seconds, one thread, relative gap 0.01 and absolute gap $50; inner book construction limits differ. American early assignment is unmodeled. Displayed-depth simulated fills do not establish broker execution. No new execution-stress scenarios were run.

This directory is a byte-preserving mirror of the generated standard reports plus a navigation note and direct saved-field extracts. The full immutable result, original attempts, models and exposure history remain under the recorded local evidence roots.
