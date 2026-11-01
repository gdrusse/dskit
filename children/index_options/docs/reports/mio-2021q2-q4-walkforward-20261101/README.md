# MIO 2021 Q2–Q4: quarterly walk-forward evaluation

All nine standard method reports are verified.

## Evaluation

Only **2 of 9** fully settled method-quarter results were profitable: Q2 shock (**$1,853.85**) and Q4 nominal (**$19,190.15**). All three Q3 methods lost money, so none was profitable in every quarter. Q2 W1 showed a positive quarter-end mark but finished with a settled loss. These nine results are correlated development comparisons, not independent evidence of a stable winner.

Q4 nominal won on only **2 of 25 filled books (8%)**. Its largest gain, the October 18 COST book expiring November 19, was **$22,287.10**, exceeding the entire quarter cohort's net profit. The positive result is concentrated and does not establish a reliable strategy.

Q4 uses the subsequently approved public-notice spinoff eligibility policy; Q2/Q3 retain their original eligibility. The table is a comparison of these documented variants, not a single unchanged-eligibility experiment. MRK in Q2 and GE in Q3 still require separate applicability/equivalence analysis before claiming one uniform support policy.

The accepted M53 predictor feeds three unchanged portfolio methods across the same 212-name universe. Each method and quarter starts its own $10,000 account, with $1,000 deposits every 14 days during that quarter. Returns across these reset accounts must not be compounded.

Quarter-end values mark open books on the final session of the named quarter. Settled cohort values include all later expiries of those books. P&L is net of fees and excludes deposits; time-weighted return adjusts for deposit timing. Generic event charts value open books at cost and are labeled accordingly; the MIO marked ledger is authoritative.

[Comparison data and source hashes](comparison.csv). Returns and drawdowns in the CSV are fractions; money is USD. It contains only completed, verified quarters.

### Q2

[Quarter report and execution detail](q2/README.md).

- [w1_003_shock10_lp](q2/w1_003_shock10_lp/summary.md) · [HTML](q2/w1_003_shock10_lp/report.html): quarter-end marked P&L $6,515.85, TWR 27.22%; settled cohort profit $1,853.85, TWR 0.88%; 22 filled books / 52 leg rows / 291 contracts.
- [nominal_lp](q2/nominal_lp/summary.md) · [HTML](q2/nominal_lp/report.html): quarter-end marked P&L $-8,964.55, TWR -63.60%; settled cohort profit $-9,242.55, TWR -65.04%; 11 filled books / 13 leg rows / 207 contracts.
- [w1_003_lp](q2/w1_003_lp/summary.md) · [HTML](q2/w1_003_lp/report.html): quarter-end marked P&L $14,381.35, TWR 79.35%; settled cohort profit $-3,473.15, TWR -26.05%; 12 filled books / 24 leg rows / 191 contracts.

### Q3

[Quarter report and execution detail](q3/README.md).

- [w1_003_shock10_lp](q3/w1_003_shock10_lp/summary.md) · [HTML](q3/w1_003_shock10_lp/report.html): quarter-end marked P&L $-3,648.15, TWR -26.24%; settled cohort profit $-5,193.65, TWR -35.47%; 21 filled books / 40 leg rows / 161 contracts.
- [nominal_lp](q3/nominal_lp/summary.md) · [HTML](q3/nominal_lp/report.html): quarter-end marked P&L $-3,864.75, TWR -29.10%; settled cohort profit $-1,303.75, TWR -14.14%; 16 filled books / 18 leg rows / 395 contracts.
- [w1_003_lp](q3/w1_003_lp/summary.md) · [HTML](q3/w1_003_lp/report.html): quarter-end marked P&L $-2,990.90, TWR -23.56%; settled cohort profit $-3,636.40, TWR -27.35%; 6 filled books / 9 leg rows / 56 contracts.

### Q4

[Quarter report and execution detail](q4/README.md).

- [w1_003_shock10_lp](q4/w1_003_shock10_lp/summary.md) · [HTML](q4/w1_003_shock10_lp/report.html): quarter-end marked P&L $-3,154.55, TWR -13.55%; settled cohort profit $-8,133.55, TWR -47.06%; 20 filled books / 31 leg rows / 167 contracts.
- [nominal_lp](q4/nominal_lp/summary.md) · [HTML](q4/nominal_lp/report.html): quarter-end marked P&L $26,057.65, TWR 215.06%; settled cohort profit $19,190.15, TWR 163.61%; 25 filled books / 33 leg rows / 609 contracts.
- [w1_003_lp](q4/w1_003_lp/summary.md) · [HTML](q4/w1_003_lp/report.html): quarter-end marked P&L $-1,769.00, TWR -3.78%; settled cohort profit $-8,135.50, TWR -46.82%; 4 filled books / 7 leg rows / 70 contracts.

## Training and early stopping

The accepted selected65 MLP64 seed29 recipe uses patience 50 and maximum 150. The earlier screening recipe with patience 10 was a different experiment. M53 had no separate formal HPO; it was chosen among 67 saved methods using pooled 2020–2023 scores. These are retrospective development results. No 2024+ price or outcome records entered model fitting or evaluation.

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

## Q4 settlement input correction

The first Q4 attempt failed at AVAV expiry on 2022-02-18. Its closing price existed, but compact chain inputs omitted later-expiry observations used to establish the settlement-day adjustment basis. The failed run remains exposed and retained. The first correction restored retained post-quarter rows outside forecast expiries. A subsequent coverage check found omitted CMCSA/HAL source members. The final source adds their observed settlement-tail quotes and uses one consistent CMCSA adjustment factor; its raw price units and nonprice fields are preserved. CMCSA adjusted prices change by about 0.000005% because the full-interval measured factor changed. Models, 212 names, three methods and trading settings are unchanged. [Initial correction](q4-tail-input-correction.json) and [final correction](q4-tail-v2-correction.json) preserve the original failure, source revisions and exposure linkage.

The supplemental source review found that an earlier claim of protected row-group exclusion was false: Arrow could scan protected-date date/symbol columns before filtering. No protected price/outcome records entered conversion. An independent regeneration using physically pre-2024 chain files and the existing sealed_from guard reproduced every market value exactly. [Boundary correction and regeneration evidence](q4-bounded-regeneration.json) retains this distinction and the original failed review.

[Q4 supplemental source review lock](q4-supplemental-review-lock.json) binds the final correction and both independent review verdicts.

## Q4 approved eligibility and retained failed attempts

The complete settlement-tail Q4 attempt failed for nominal_lp at the IBM/Kyndryl mixed deliverable; its two successful method reports remain in [q4-partial](q4-partial/README.md). That failure was published once and then abandoned with its exposure retained. [Failure evidence](q4-partial-failure.json) and [partial report correctness](q4-partial-report-correctness.json) / [integration](q4-partial-report-integration.json) preserve that attempt.

The owner then approved a config-only public-notice eligibility policy. Existing JSON Derive/Filter nodes exclude122paired forecast/context candidates (92IBM,30O) whose expiries reach publicly announced unsupported distributions. The same81,821retained candidates feed every method. Full quote/basis history, retained prediction values, fitted models, frozen M53 weights, all212names and portfolio economics are unchanged. The first quote-filter proposal was rejected before a MIO claim because it removed needed settlement metadata; the corrected variant retains the complete chain.

IBM exclusions start October13 following the October12 announcement and end when standard IBM resumes November5; O exclusions start October25 after the October22 announcement and end at standard O resumption November16. Release notices were public before resumption. No realized profit, future price or future measured basis is an eligibility predicate. Post-action standard identity assumes Theta honors documented exact-symbol request semantics. Unsupported holdings opened before a notice would still refuse; no backdated repair is applied.

[Corrected source and exact subset evidence](q4-eligibility-v2-source.json), [independent design assessment](q4-eligibility-forecast-phase0.json) and [prior-exposure linkage](q4-eligibility-prior-exposure.json) retain the approved scope, all original producer/training evidence and historical limitations. The revised policy was adopted after prior Q4 outcomes were exposed; all results remain retrospective development.

[Execution and correction memo](../../memos/2026-11-01-mio-q2-q4-walkforward.md) records storage causes, training/HPO findings, retained failures, automation limits and reproducibility.

## Final output review

The nine final reports, combined evaluation and execution memo passed fresh sequential [correctness/accounting](final-output-correctness.json) and [integration/provenance](final-output-integration.json) reviews with zero unresolved Critical/Major findings. [Final output lock](final-output-review-lock.json) records the candidate, checks and preserved dependencies.

The generic report phrase “14 sessions” refers to decision dates; the explicit quarter tables and daily ledgers correctly show 63/64/64 account sessions. This known wording Minor remains disclosed. The memo's storage-observation timestamp was corrected to the retained 15:09 UTC observation; no financial result changed. The lock hashes the reviewed `6b795948` versions of this README, the memo and combined-status.json; this closure section and the memo Closure were added afterwards and checked separately. Earlier scoped reviews remain intact, and earlier pending-state text is in Git history.
