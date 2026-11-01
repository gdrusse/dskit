# MIO 2021 Q2–Q4: quarterly walk-forward evaluation

3 of nine standard method reports are verified. Remaining quarters: Q3, Q4.

## Evaluation

The accepted M53 predictor feeds three unchanged portfolio methods across the same 212-name universe. Each method and quarter starts its own $10,000 account, with $1,000 deposits every 14 days during that quarter. Returns across these reset accounts must not be compounded.

Quarter-end values mark open books on the final session of the named quarter. Settled cohort values include all later expiries of those books. P&L is net of fees and excludes deposits; time-weighted return adjusts for deposit timing. Generic event charts value open books at cost and are labeled accordingly; the MIO marked ledger is authoritative.

[Comparison data and source hashes](comparison.csv). Returns and drawdowns in the CSV are fractions; money is USD. It contains only completed, verified quarters.

### Q2

[Quarter report and execution detail](q2/README.md).

- [w1_003_shock10_lp](q2/w1_003_shock10_lp/summary.md) · [HTML](q2/w1_003_shock10_lp/report.html): quarter-end marked P&L $6,515.85, TWR 27.22%; settled cohort profit $1,853.85, TWR 0.88%; 22 filled books / 52 leg rows / 291 contracts.
- [nominal_lp](q2/nominal_lp/summary.md) · [HTML](q2/nominal_lp/report.html): quarter-end marked P&L $-8,964.55, TWR -63.60%; settled cohort profit $-9,242.55, TWR -65.04%; 11 filled books / 13 leg rows / 207 contracts.
- [w1_003_lp](q2/w1_003_lp/summary.md) · [HTML](q2/w1_003_lp/report.html): quarter-end marked P&L $14,381.35, TWR 79.35%; settled cohort profit $-3,473.15, TWR -26.05%; 12 filled books / 24 leg rows / 191 contracts.

## Training and early stopping

The accepted selected65 MLP64 seed29 recipe uses patience 50 and maximum 150. The earlier screening recipe with patience 10 was a different experiment. M53 had no separate formal HPO; it was chosen among 67 saved methods using pooled 2020–2023 scores. These are retrospective development results. No 2024+ data were read for this study.

Epochs were selected on chronological pre-quarter monitoring data. The outer diagnostic validation bands below did not select epochs. Q3 and Q4 were never epoch-selection validation bands. After selection, the model was freshly fitted on all mature pre-quarter training rows for the selected number of epochs. The final refit transfers epochs, not the number of optimizer updates; the larger training set yields more updates.

- preq2: selected **epoch 46** after 96 epochs. Fit origins 2017-01-03–2020-12-23; monitor origins 2020-12-28–2021-02-25, labels settled through 2021-02-26; diagnostic validation origins 2021-03-01–2021-03-25. Monitor loss: before training 1.049935, epoch 1 1.020467, selected epoch 1.013974.
- preq3: selected **epoch 36** after 86 epochs. Fit origins 2017-01-03–2021-03-25; monitor origins 2021-04-01–2021-05-27, labels settled through 2021-05-28; diagnostic validation origins 2021-06-01–2021-06-24. Monitor loss: before training 0.822575, epoch 1 0.793174, selected epoch 0.789470.

[Learning diagnostics](learning.csv) show finite gradients and nonzero parameter changes. This confirms that training updated the model and improved the monitored objective; it does not prove trading profitability. The corrected coverage selects epochs 46 and 36, superseding the earlier epoch-4 attempt.

- Q2 final fit: 46 epochs; 1,220,953 mature training rows; origins through 2021-03-25, labels settled through 2021-03-26.
- Q3 final fit: 36 epochs; 1,302,290 mature training rows; origins through 2021-06-24, labels settled through 2021-06-25.
- Q4 final fit: 36 epochs; 1,383,824 mature training rows; origins through 2021-09-23, labels settled through 2021-09-24.

Q4 includes mature Q3 outcomes in training and retains the pre-Q3 choice of 36 epochs. Its model does not reuse old fitted weights. M53 is fitted once at each quarter start using only prior forecast outcomes already settled at that time, then frozen during the quarter. A prior quarter forecast row named val may teach the next quarter once its outcome is known; it does not select the next quarter neural epoch count.

## Verification and limits

- Each quarterly result requires an attested close receipt, all three completed methods, recorded replay parity, complete quarter calendars, no remaining open books after settlement, and reconciled trade profit, fees, contract counts and equity. The standard reports use MioRunRows → RowsToEvents → EventTotalsGate → EvaluationReport → ArtifactLinks. Money gates allow $0.005; count gates are exact.
- The reports retain original selected leg quantities, actual fills, refusals, fees, expiry outcomes and every marked account session. A book can contain several legs; contracts count absolute executed leg quantities. Fill-event counts also include the closing events.
- Both independent implementation/config reviews passed with zero Critical or Major findings: [correctness](final-correctness.json), [integration](final-integration.json), [candidate lock](review-lock.json). The focused CDF tests previously passed 86 with 2 skipped; no full suite was rerun.
- Two disclosed minor items remain: the regression test fixture could add an unsettled-label row, although independent boundary probes passed; and Q4 selection_summary_sha256 names the verification wrapper whose embedded summary_sha256 identifies the correct pre-Q3 summary. The immutable receipt was retained.
- The outer solver uses 900 seconds, one thread, relative gap 0.01 and absolute gap $50. Inner book construction limits differ. Displayed-depth simulated execution and daily marks are not broker fills. American early assignment is unmodeled. No new execution-stress scenarios were run.
- Earlier 2019/Q1 runs had truncated coverage; this Q2–Q4 correction does not repair them. Original attempts, failures, prior exposure, raw sources, model artifacts and full results are retained. The model-selection history prevents calling 2021 a fresh holdout.

[Protocol and training evidence](protocol-evidence.json) includes the prior-exposure linkage and exact model proof hashes. Each quarter index binds source result/receipt, report configuration, renderer commit and generated file hashes.
