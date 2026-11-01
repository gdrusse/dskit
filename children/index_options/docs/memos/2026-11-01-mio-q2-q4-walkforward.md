# MIO Q2 to Q4 2021 execution and correction memo

## TL;DR

All three methods lost money on the fully settled Q3 cohort, so none was profitable in every quarter of this Q2–Q4 comparison. These are retrospective development results, and Q4 uses a newly approved contract-eligibility policy.

All three quarterly simulations and all nine standard method reports are complete and independently verified. Both final output reviews have zero unresolved Critical or Major findings.

## Execution contract

The owner authorized Q2–Q4 2021 simulations, reports, this memo, independent review, wrap, merge, push and safe task cleanup. The accepted methods are w1_003_shock10_lp, nominal_lp and w1_003_lp, with the same 212-name universe and M53 predictive family. Each method and quarter starts a separate $10,000 account and receives $1,000 every 14 days during the quarter, totaling $16,000 contributed. Fees are $0.65 per executed contract. Decisions and fills follow the existing pinned weekly and next-session execution rules.

Q2 covers April 1–June 30, Q3 July 1–September 30 and Q4 October 1–December 31. Expected account-session counts are 63, 64 and 64, with 14 decision dates each. Account history continues after quarter end until positions settle; those extra rows are not new-quarter entries.

Q4's approved eligibility policy changes which candidate trades may be entered. Q2/Q3 retain their original eligibility, so the quarters are not one uniform eligibility experiment. The Q4 eligibility change preserves its fitted model and blend, portfolio methods, universe and economics. Path A0002/A0003/A0008 and protected-data boundaries are unchanged; no Path or TDD rows were rewritten.

The outer optimizer has a 900-second limit, one thread, relative objective gap 0.01 and absolute gap $50. Inner book-construction limits differ. Optimizer tolerances do not bound investment losses. Fills use simulated displayed depth; American early assignment remains unmodeled.

## Empirical results

### Q2

- w1_003_shock10_lp: quarter-end marked profit **$6,515.85**; settled cohort profit **$1,853.85**; 22 filled books, 52 leg rows, 291 executed contracts, 13 refused books, and $189.15 fees.
- nominal_lp: quarter-end marked profit **$-8,964.55**; settled cohort profit **$-9,242.55**; 11 filled books, 13 leg rows, 207 executed contracts, 15 refused books, and $134.55 fees.
- w1_003_lp: quarter-end marked profit **$14,381.35**; settled cohort profit **$-3,473.15**; 12 filled books, 24 leg rows, 191 executed contracts, 10 refused books, and $124.15 fees.

[Q2 report and execution detail](../reports/mio-2021q2-q4-walkforward-20261101/q2/README.md).

### Q3

- w1_003_shock10_lp: quarter-end marked profit **$-3,648.15**; settled cohort profit **$-5,193.65**; 21 filled books, 40 leg rows, 161 executed contracts, 11 refused books, and $104.65 fees.
- nominal_lp: quarter-end marked profit **$-3,864.75**; settled cohort profit **$-1,303.75**; 16 filled books, 18 leg rows, 395 executed contracts, 28 refused books, and $256.75 fees.
- w1_003_lp: quarter-end marked profit **$-2,990.90**; settled cohort profit **$-3,636.40**; 6 filled books, 9 leg rows, 56 executed contracts, 15 refused books, and $36.40 fees.

[Q3 report and execution detail](../reports/mio-2021q2-q4-walkforward-20261101/q3/README.md).

### Q4

- w1_003_shock10_lp: quarter-end marked profit **$-3,154.55**; settled cohort profit **$-8,133.55**; 20 filled books, 31 leg rows, 167 executed contracts, 25 refused books, and $108.55 fees.
- nominal_lp: quarter-end marked profit **$26,057.65**; settled cohort profit **$19,190.15**; 25 filled books, 33 leg rows, 609 executed contracts, 34 refused books, and $395.85 fees.
- w1_003_lp: quarter-end marked profit **$-1,769.00**; settled cohort profit **$-8,135.50**; 4 filled books, 7 leg rows, 70 executed contracts, 19 refused books, and $45.50 fees.

Q4 nominal won on only **2 of 25 filled books (8%)**. Its largest gain, the October 18 COST book expiring November 19, was **$22,287.10**, exceeding the entire quarter cohort's net profit. The positive result is concentrated and does not establish a reliable strategy.

[Q4 report and execution detail](../reports/mio-2021q2-q4-walkforward-20261101/q4/README.md).


[Combined evaluation](../reports/mio-2021q2-q4-walkforward-20261101/README.md) and [comparison CSV](../reports/mio-2021q2-q4-walkforward-20261101/comparison.csv) give returns, drawdowns, coverage and exact source identities. A book groups the legs of one ticker/expiry position; a leg row is one option instruction; contracts count absolute executed leg quantities. Refused books are separate.

Quarter-end profit includes marked open positions. Settled cohort profit includes all later expiries of positions opened that quarter. For Q2 w1_003_lp, final equity minus deposits is $12,526.85 − $16,000.00 = **$-3,473.15** after settlement. Its quarter-end marked profit was **$14,381.35**, with 6 books still open. The earlier positive marked figure was not a fully settled gain.

Time-weighted return adjusts for deposit timing; it is not profit divided by total deposits. Independent quarterly returns must not be compounded. Generic event charts hold open books at cost; the MIO marked ledger supplies authoritative market-value returns and drawdowns.

## Training and the HPO question

The accepted base model is selected65 MLP64, seed 29. Its recipe uses patience 50 and maximum 150 epochs. Patience counts epochs without a new best monitored loss; 150 is a ceiling. The patience-10 configuration belonged to an earlier screening experiment.

M53 had **no separate formal HPO**. It was selected among 67 saved methods using 2020–2023 scores. This makes the current 2021 results retrospective development evidence even though each quarter's new weights and blend use only earlier settled outcomes.

- Before Q2, early stopping selected **epoch 46** from 96 run epochs. Fit origins end December 23, 2020; monitoring origins are December 28, 2020–February 25, 2021, with labels settled through February 26. The March 1–25 diagnostic band did not select epochs.
- Before Q3, it selected **epoch 36** from 86. Fit origins end March 25, 2021; monitoring uses April 1–May 27, with labels settled through May 28. The June 1–24 diagnostic band did not select epochs.
- Fresh final fits used **46 / 36 / 36** epochs for Q2/Q3/Q4 and **1,220,953 / 1,302,290 / 1,383,824** mature rows. Latest training-label settlement dates were March 26, June 25 and September 24 respectively.
- Q4 includes mature Q3 outcomes and fresh model weights. It retains the pre-Q3 epoch choice because the owner excluded Q3/Q4 from neural epoch selection. M53 blend weights are fitted at each quarter start using only previously settled forecast outcomes, then frozen.

The selections supersede the earlier epoch-4 attempt. Pre-Q2 monitor loss fell from 1.049935 before training to 1.020467 at epoch 1 and 1.013974 at epoch 46. Pre-Q3 values were 0.822575, 0.793174 and 0.789470 at epoch 36. Finite gradients and nonzero weight changes confirm learning on the monitored score; they do not establish trading profitability. Epoch count alone would not establish an absence of learning. [Training proofs](../reports/mio-2021q2-q4-walkforward-20261101/protocol-evidence.json) and [learning diagnostics](../reports/mio-2021q2-q4-walkforward-20261101/learning.csv) retain the exact summaries.

## Storage cause and repair

The investigation found 2,066 repeated run-local quote-index paths under artifacts/mio-quotes, totaling about 46.4 logical GiB. Byte-identical SQLite indexes were rebuilt across runs. Verified hardlink deduplication recovered about 34 GiB inside WSL. The existing ADR-0285 cache repair reuses indexes only when full source, predicates, validators, code, dependencies and content hashes match. The current Q4 run can reuse all 212 existing quote indexes.

Free Linux blocks did not shrink the Windows virtual-disk file, whose recorded logical size remained about 811.89 GiB. Offline compaction required administrator approval and a recorder pause; it was not performed in this continuation. Lossless compression of selected Windows files restored headroom without deleting source data or stopping recorders. Windows free space was observed at 18.625 GiB at 09:45 UTC and about 10.01 GiB at 15:09 UTC on November 1. Concurrent writes and other changes mean the entire net change cannot be attributed to compression.

Unique cache indexes persist after run links disappear. Deleting a hardlink is not necessarily a physical saving, and WSL deletion does not promise immediate C: recovery. [Storage evidence](../reports/mio-2021q2-q4-walkforward-20261101/storage-evidence.json) and existing ADR-0285 retain the measurements and limitations.

## Corrections and retained failures

1. **Standard splits.** The earlier Q2 TTD position crossed its 10-for-1 split. The owner-approved ADR-0288 implementation added pinned standard-split conversion across holdings, marks, reserve and settlement, preserving original entry quantities and fees. Its failed attempt remains exposed. Mixed-share spinoffs are a different contract type.
2. **Incomplete time coverage.** Earlier prepared inputs did not cover every requested session. Complete-quarter sources and fresh chronological fits replaced those attempts under separate identities. Older 2019/Q1 coverage remains truncated and was not repaired here.
3. **Q4 settlement inputs.** Q4 first failed at AVAV's February 18, 2022 expiry. Its closing price existed, but compact quote inputs omitted observations needed for that day's adjustment basis. Corrected inputs retain the full settlement tail. A later check restored CMCSA/HAL source members and a consistent CMCSA basis. [Evidence](../reports/mio-2021q2-q4-walkforward-20261101/q4-tail-v2-correction.json).
4. **Protected-date claim.** An earlier review incorrectly claimed protected row groups were excluded before scanning. Date/symbol metadata could be scanned before filtering; protected price/outcome values did not enter conversion. Regeneration from physically pre-2024 files with the existing boundary guard reproduced the values. The original claim and [correction](../reports/mio-2021q2-q4-walkforward-20261101/q4-bounded-regeneration.json) remain retained.
5. **IBM settlement failure.** Tail-corrected Q4 completed shock and w1 but failed nominal on a contract delivering 100 IBM plus 20 Kyndryl shares. The pipeline finished, but its actual study receipt says complete=false. Its two reports and absent nominal totals remain [visible](../reports/mio-2021q2-q4-walkforward-20261101/q4-partial/README.md). The claim was published once and abandoned once before the new eligibility run.
6. **Approved entry eligibility.** The owner approved avoiding new contracts that cross publicly announced unsupported spinoffs. Dated rules cover IBM/Kyndryl and Realty Income/Orion, use the next session after date-only announcements, and retain verified standard contracts after resumption. Existing JSON derive/filter nodes remove 122 paired forecast/context rows, 92 IBM and 30 Realty Income; 81,821 remain. All three methods use this source. [Rule and source proof](../reports/mio-2021q2-q4-walkforward-20261101/q4-eligibility-v2-source.json).
7. **Rejected filtering correction.** The first proposal filtered chain quotes and removed needed settlement-basis rows, including valid pre-action IBM settlement. Review rejected it before MIO execution. The corrected approach filters paired candidates while retaining the full quote/basis source. Both fresh reviews reported zero findings. [Launch lock](../reports/mio-2021q2-q4-walkforward-20261101/q4-eligibility-v2-review-lock.json).
8. **Stale report references.** Initial Q2/Q3 report copies retained template provenance. Corrected reports bind actual receipt, result and config hashes. Their 48 numerical CSVs remained byte-identical; report regeneration did not rerun simulations. [Independent correction review](../reports/mio-2021q2-q4-walkforward-20261101/report-provenance-integration-amendment.json) closed the finding.

Unsupported positions opened before an announcement are not retroactively removed; their existing refusal remains. Standard-root identity depends on pinned provider requests and exchange resumption evidence; normalized quotes lack an independent deliverable column. Applying equivalent eligibility to earlier Q2/Q3 actions was not performed, so cross-quarter policy comparisons remain qualified.

## Automation and verification

Simulation uses existing JSON plan → freeze → open → intake → study → close nodes. Reports use MioRunRows → RowsToEvents → EventTotalsGate → EvaluationReport → ArtifactLinks. Orchestration verifies actual completion, replay, calendars, source hashes and accounting before exactly-once publication/review.

The full date-change-to-data-preparation-to-report chain was not a single automated entry point. Source preparation, binding and report coordination required orchestration. Several corrections were avoidable because initial checks did not cover the entire requested input and report chain before expensive runs. The first eligibility filter was an implementation error. Completed configs do not establish that an arbitrary future date change is turnkey.

Earlier implementation changes and the final configuration-only eligibility revision have separate review scopes. Retained focused results include 402 split-ledger tests and 86 CDF tests with 2 skips. No full suite was rerun. Each final method report has seven reconciliation gates, using $0.005 money tolerance and exact counts. They verify faithful reporting, not a fresh independent payoff model.

This final revision added no new ADR, mixed-share runtime support, fresh stress campaign, corrected 2019/Q1 rerun, broker execution or 2024+ price/outcome fitting or evaluation. Raw sources, models, original/failed evidence, registries and live recorders are preserved.

## Reproducibility and handoff

Execution root: /home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2. Its continuation.json is the current handoff; historical evidence keeps its then-current status. Worktree: /home/russell/wt/q2-reconcile-20261031, branch codex/q2-reconcile-20261031. Execution is in WSL via SSH, using /home/russell/dskit/.venv/bin/python, PYTHONPATH set to the worktree and children/index_options, and OMP/MKL/OPENBLAS threads 1.

The corrected Q4 source/config candidate is ae2c9f986fb6fb88e07a3dc362b5873813fd7d23. Run-JSON SHA-256: 502417df96f227f37dea168753991d9e387497ce1f729abd60302a82b6461d2a. Pipeline document hash: 4f1bed0c95e02f20548eef8b462df50e583d3c1c4dc33f1110839624efde3987. These identify different serialized objects. All 270 runtime files were verified recoverable from that Git candidate.

The executed command uses the existing CLI: /home/russell/dskit/.venv/bin/python -m dskit.pipeline run children/index_options/configs/mio-2021h2-walkforward-20261101/run-mio-2021q4-eligibility-v2.json --asof 2021-12-31, from the worktree with the environment above. This records execution, not an instruction to reopen the completed claim.

Quarterly indexes bind receipts, generated reports, numerical CSVs and orders/fills. Reuse immutable results for report regeneration. A new authorized replay needs a new identity and prior-exposure linkage.

## Closure

The final output candidate is 6b795948d83b36393410532c6ca60b5c89c2ed79. Fresh sequential correctness/accounting and integration/provenance reviews both passed with zero Critical/Major findings. They reconciled all 63 report gates, 137 filled books, 227 leg rows, 2,147 executed contracts, 170 refusals, 676 selected-leg records and 984 daily account rows. [Final review lock](../reports/mio-2021q2-q4-walkforward-20261101/final-output-review-lock.json) retains both independent verdicts, exact artifact identities and limits.

The reported storage-observation time is corrected to 15:09 UTC using the retained observation. The generic report headline's “14 sessions” means decision dates; explicit coverage tables and daily ledgers correctly show 63/64/64 quarterly account sessions. That wording remains a disclosed nonblocking Minor, alongside the two earlier CDF evidence/fixture minors. Financial outputs, methods, models, sources and runtime are unchanged by this closure.

Reports, all task configs and this memo are retained outside the checkout under coverage-v2/delivery, preserving repository-relative links. Original and failed runs, raw sources, model artifacts, exposure registries and live recorders are retained. The completed claim must not be rerun. Restore the committed report candidate at the original checkout path when exact absolute runtime pins are needed for reproduction.

The requested wrap includes a nonforce main push, remote containment verification and removal of the contained task branch/checkout. The push and cleanup receipts are written after this commit to coverage-v2/continuation.json and coverage-v2/delivery/wrap-receipt.json, outside the repository. Those operational receipts are separate from the immutable reviewed simulation and report artifacts. General future-date automation, repaired 2019/Q1 coverage and live execution remain outside this completed evaluation.
