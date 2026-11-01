# MIO 2021 Q4: complete-quarter walk-forward evaluation

Three independent broad212 accounts; 64 quarter sessions and 14 weekly decisions each. M53 is frozen at quarter start. All three attested replays and standard report accounting gates passed.

## Quarter end and later settlement

Quarter-end figures mark open options at the saved daily MIO marks. Settled cohort profit includes the later expiries of positions opened during this quarter. Dollar profit includes fees and excludes deposits. TWR adjusts for scheduled deposits; it is not dollar profit divided by total contributions.

- [w1_003_shock10_lp](w1_003_shock10_lp/summary.md) · [HTML](w1_003_shock10_lp/report.html) · [marked ledger](w1_003_shock10_lp/ledger.csv): quarter-end marked P&L **$-3,154.55**, TWR **-13.55%**, 8 open books; settled profit **$-8,133.55**, TWR **-47.06%**. Filled 20 books / 31 leg rows / 167 contracts; refused 25 books; fees $108.55.
- [nominal_lp](nominal_lp/summary.md) · [HTML](nominal_lp/report.html) · [marked ledger](nominal_lp/ledger.csv): quarter-end marked P&L **$26,057.65**, TWR **215.06%**, 9 open books; settled profit **$19,190.15**, TWR **163.61%**. Filled 25 books / 33 leg rows / 609 contracts; refused 34 books; fees $395.85.
- [w1_003_lp](w1_003_lp/summary.md) · [HTML](w1_003_lp/report.html) · [marked ledger](w1_003_lp/ledger.csv): quarter-end marked P&L **$-1,769.00**, TWR **-3.78%**, 2 open books; settled profit **$-8,135.50**, TWR **-46.82%**. Filled 4 books / 7 leg rows / 70 contracts; refused 19 books; fees $45.50.

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

## Approved Q4 eligibility change

This Q4 rerun uses the owner-approved public-announcement rule uniformly across all three methods. IBM/Kyndryl crossing contracts are excluded from October 13 until standard IBM contracts resume November 5; Realty Income/Orion crossings are excluded from October 25 until standard O resumes November 16. Expiry must reach the announced distribution date. The rule uses no future prices or realized profit. All quote and basis data remain unchanged. The same122paired candidate forecast/context rows (92IBM,30O) are excluded for every method;81,821remain. Retained prediction values, all212names, models and economics remain fixed.

Post-action standard identity assumes Theta honors its documented exact-symbol API semantics; normalized files have no independent deliverable field. Existing settlement guards still refuse unsupported preannouncement holdings.

Q2/Q3 remain the original eligibility runs. MRK in Q2 and GE in Q3 need separate applicability/equivalence analysis before claiming a uniform support policy across quarters. Do not treat the combined table as one unchanged-eligibility experiment. Original Q4 AVAV failure and IBM partial failure, including two completed method reports, are retained under q4-partial and linked evidence. This new variant was approved after those exposures.
