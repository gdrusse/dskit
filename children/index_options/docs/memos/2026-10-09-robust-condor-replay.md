# PatchTST equity-condor research replay — 2026-10-09

## TL;DR

The authorized replay completed all ten stages once, with exit code 0. Its nominal-radius comparison settled 131 condors for **$5,354.85 net research P&L**, but all seven calibrated arms abstained: none of the ten monthly updates produced a supported radius. Same-day look-ahead fills, a largest win exceeding total profit, restricted price-basis coverage and 1,128 strict solver-certificate refusals prevent treating this as evidence of a deployable robust strategy.

## Frozen execution contract

The [evaluation scheme](2026-10-08-forecast-mio-evaluation-scheme.md) gives the exact training, early-stopping, feature-selection, HPO, confirmation and nine chronological checkpoint windows. The [dual memo](2026-10-08-robust-condor-dual.md) publishes the primal/dual/MILP corresponding to the [controlling formulation](../research/distribution-modeling/simple-formulation-with-robustification.md) and [worked explanation](../explanations/robust-condor-selection.md). The [run configuration](../../configs/run-equity-condor-robust-backtest.json) is authoritative for settings.

This run reused 99,225 archived nominal seed11 forecasts from the date-appropriate PatchTST checkpoints. It performed no new fits, HPO, seed selection or checkpoint selection by profit. The [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) identifies all 66 preserved model packages, their exact inference/backup paths and hashes. Historical dates must use their corresponding checkpoint; the latest package cannot be applied retrospectively.

Calibration origins were 2024-02-06 through 2024-12-31. Entry origins were configured for 2025-02-04 through 2025-11-28, with actual admitted input dates ending November 25. Expiry was exactly 31 calendar days, with bounded settlement confirmation strictly before 2026. Monthly updates used only already-settled, assumed-published fixed audit-template outcomes, including prior entry-phase templates whether or not the optimizer traded.

One lot means 100 shares per option leg. Four ordered strikes were chosen from all eligible combinations using the frozen MILP, with a no-trade alternative and secondary tie solve. Entry fills use that day's VWAP with a same-day-close forecast: this is explicitly a same-day look-ahead research assumption. Liquidity-tier haircuts are assumed, fees are $0.65 per entry leg, early assignment is ignored, and no portfolio capital or overlapping-position limit is modeled. Qlo/Qhi bands were disabled; CDF/W1 projection limits were diagnostic-only. Protected 2026 market outcomes were not retained or scored. Authorized action dates/ratios were used solely to reconstruct historical split basis.

## Actual results and denominators

The [summary](../reports/robust-condor-replay-20261009/summary.json), [complete ticker aggregates](../reports/robust-condor-replay-20261009/ticker-aggregates.json) and [monthly calibration evidence](../reports/robust-condor-replay-20261009/calibration.json) retain the full aggregate accounting, including empty arms and refusals. Private artifacts retain individual forecasts, decisions, fills and certificates; those are not published.

Input preparation retained 655,174 raw option rows, of which 644,799 belonged to the modeled universe. Sixteen option-source symbols were outside that universe; four modeled names lacked chain records. Thus 388 of 392 intended forecast symbols had accepted chain rows, not 392 universally eligible trading histories. All 99,225 forecast records remained represented.

Before projection, template and leg-liquidity checks, matched price-basis/chain coverage was 2,796 calibration contexts across 72 symbols/48 dates and 2,772 entry contexts across 79 symbols/43 dates. Price reconstruction depends on the completeness assumption for 84 explicit action inventories. The 307 missing-inventory histories were refused rather than labeled “no split”; AMZN source-vintage inconsistency and known action overlaps were also explicit refusals. These gates materially restrict the evaluated population. Of 45,023 entry-window forecasts, 9,668 belong to the original85 cohort and 35,355 to restored307. All 35,355 restored-cohort contexts were refused before selection. Every actual trade belongs to the original85 cohort; this replay supplies no trading-performance estimate for restored307.

Of those 45,023 entry forecasts, 42,271 had shared projection refusals and 2,752 remained. The nominal arm had 2,710 decisions: 1,451 no-trades, 1,128 uncertified-primary refusals and 131 trades; another 42 contexts lacked a calibration-derived liquidity tier. Each of the seven calibrated arms—base rho, zero/half/double haircut and liquidity thresholds 3/10/20—had 2,710 missing-radius refusals plus the same 42 missing-tier refusals. They produced no trades. Their booked P&L is zero by abstention; mean return and comparative strategy performance are unavailable.

Shared projection refusals are counted once, separately from arm-specific refusals. The all-phase projection total of 93,726 must not be used as the entry-window denominator or multiplied by eight. Decision rows marked skipped and their matching refusal records describe the same event.

## Nominal comparison performance

The 131 settled condors span 40 symbols and 43 entry dates. There were 71 wins and 60 losses: 54.20% wins, mean net P&L $40.88 per one-lot trade. Median net P&L was $33.29; the empirical fifth percentile was -$2,385.39 (unweighted, linear/type-7 interpolation across 131 outcomes, not a future risk guarantee). P&L before entry fees was $5,695.45, still incorporating the assumed fill haircuts; fees totaled $340.60. The assumed entry haircuts cost $11,893.07 across the 524 selected legs relative to their own-day VWAPs. This holds the chosen legs fixed; it is not the profit of a newly selected no-haircut strategy. The worst trade lost $4,047.74; the best gained $5,623.66. Removing that single best outcome would leave **−$268.80**. This concentration calculation is descriptive, not a proposed alternate trading rule.

Every selected condor settled, all ended flat, and WindowBook P&L equaled the independently written closed-form payoff exactly in the saved evidence. The 1,048 order records and 1,048 fill records represent entry and expiry legs of 131 condors, not 1,048 independent trades. Complete per-ticker and distribution statistics are in the appendices. No annualized return, portfolio Sharpe, causal model benefit or independent profitability claim is supported.

## Why rho remained unsupported

For each fixed template and radius, residual loss is realized loss minus worst-case expected loss, in USD/share. Residuals are averaged within each date and then equally across dates. The frozen grid is 0, 0.001, 0.0025, 0.005 and 0.01 of spot. The smallest radius can pass only if both the full observed mean and the resampled upper bound are nonpositive and the sampler supports every history date.

The run produced 1,374 templates: 667 in calibration and 707 in the entry phase, on 48 and 43 dates respectively. It cached 6,870 template/radius loss values. Monthly updates reuse these identities; bootstrap does not solve the LP again.

At the first update, 667 prior templates over 48 dates supplied 43 primary 31-day blocks and 39 sensitivity 62-day blocks. Nevertheless, December 31 had zero inclusion probability. The frozen sampler uses complete, nonwrapping, half-open calendar blocks starting at observed dates. Sparse dates, gaps and the trailing edge leave some history dates outside every eligible block. All ten primary and ten sensitivity updates failed this support condition; each radius and upper bound remained null.

First-month observed mean residuals were +0.784241, +0.553905, +0.256380, −0.159524 and −0.852816 USD/share in grid order. Larger radii made the average loss estimate more conservative, but a negative average does not establish the required upper bound. It would be invalid to choose 0.005 or 0.01 from those means alone. The independent calibration review found expected refusal under the frozen contract, not an implementation failure.

Had support existed, the configured 500-resample rule would use an upper empirical quantile with alpha 0.05 allocated across five radii, alongside the separate full-mean check. It is an approximate dependence-aware diagnostic on fixed audit templates, not a simultaneous conditional-CDF guarantee for every MILP-selected trade. Neither rho nor Qlo/Qhi was empirically qualified here.

## Solver and implementation evidence

The run retained 4,292 certificates: 2,710 primary solves and 1,582 secondary solves. All reported status “ok” and termination “optimal,” but the frozen acceptance rule also required relative gap exactly zero and absolute gap no greater than $0.000001. All 1,128 rejected primaries exceeded zero relative gap while satisfying the absolute criterion. Their relative gaps ranged from 1.12e−16 to 6.59e−9; maximum absolute discrepancy was $1.68e−7.

These are numerical certificate refusals, not accepted trades or evidence that the solver crashed. They materially affect the evaluated population. The full log also retains HiGHS small-matrix-coefficient warnings; “exit 0” does not mean warning-free execution. No threshold was relaxed and no replay was repeated after observing these results.

Previously locked focused implementation checks covered formulation parity, projection, calibration timing, batch arms and settlement accounting; adapter evidence records 1,413 affected cases and the null-threshold change adds 47 focused cases. Two independent execution-binding reviews closed with zero findings. One earlier Minor permanent-test gap remains disclosed: positive settlement with a matching carried label; an independent direct check reconciled $197.40. The full test suite was not run. The final report checks and scoped reviews add evidence without changing runtime, configuration or prior locks.

## Production-realism audit — 2026-10-09

**Conclusion: this is a reproducible historical model/optimizer experiment, but it is not yet a faithful simulation of executable production trading.** Several differences can change which trades occur and their P&L; they are not merely deployment settings. This audit inspected the frozen JSON, ExactDteBarChain, RobustCondorBatchSelect, HeldOutRhoCalibration and CondorExpirySettle, plus the saved outcomes. It did not rerun or revise the experiment.

What already resembles production is the chronological checkpoint schedule, saved preprocessing and identities, expanding-history retraining protocol, prior-only monthly calibration updates, explicit abstention, ordered-strike MILP, one-lot units, entry fees and exact payoff accounting. These components and the reviewed primal/dual mathematics can be retained. However, fresh inference and timed retraining were represented by previously saved forecasts: this replay did not measure a running service's end-to-end latency or reliability.

The production differences, in order of practical importance, are:

1. **Decision and fill timing — material.** The forecast incorporates the entry day's close, while the fill is that same day's whole-session VWAP. That VWAP cannot be obtained after observing the close, and each option leg's daily VWAP need not represent a simultaneous four-leg price. Production must declare an information cutoff, an order-submission time and a later executable price. Moving entry to a later session also requires reconciling the forecast's target expiry with the remaining DTE; simply shifting a date without checking the 31-day forecast contract would create a different mismatch. The reported profit cannot quantify or correct this timing advantage.

2. **Fill probability, liquidity and costs — material.** The source contains daily trade bars, not a contemporaneous executable bid/ask book. Trade count and volume filters, plus the assumed 2/4/8% haircuts and price floors, are useful research proxies; they do not establish spread, available size, synchronized leg execution, partial fills, rejection or cancellation behavior. The $11,893.07 haircut charge is substantial but does not prove it compensates for those effects or the timing issue. A production simulation needs an explicit execution policy, observable prices after the decision, handling of unfilled/partly filled orders and the actual broker/venue fee schedule. The current $0.65 entry-leg fee and zero synthetic expiry fee are assumptions. Execution price and execution itself are distinct; a limit price does not guarantee a fill ([SEC order guidance](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders)).

3. **American exercise, assignment and stock delivery — material.** The replay closes every option leg synthetically at intrinsic value and charges no expiry fee. Standard equity options can be exercised before expiry and assignment can create a stock-delivery obligation; short and long legs do not automatically resolve as one riskless operation ([OCC/OIC assignment guidance](https://www.optionseducation.org/referencelibrary/faq/options-assignment), [exercise guidance](https://www.optionseducation.org/referencelibrary/faq/options-exercise)). Keeping the approved hold-to-expiry strategy in production therefore requires actual exercise/assignment rules, broker cutoffs and resulting stock/cash positions, including relevant dividends, funding and delivery costs. The 131 end-flat checks prove the simulated accounting, not that real brokerage positions would necessarily finish flat. Changing the strategy to close before expiry would require a separate decision and evaluation; it was not done here.

4. **Portfolio capital and overlapping positions — material if used as an account strategy.** Each ticker-date trade is chosen independently. The saved nominal trades imply up to **24 concurrent condors**, up to **3 concurrent condors on one symbol**, and **6 entries on one date**. Counts use entry-inclusive/settlement-exclusive holding intervals; they are not a margin calculation. No account capital, buying-power reservation, aggregate concentration limit, daily mark-to-market, financing or forced-liquidation process constrains selection. Production needs those account-level rules, or a deliberately restricted single-trade use case. Summing the 131 payoffs does not establish an achievable account return.

5. **Robust calibration and numerical acceptance — unresolved research requirements.** All ten primary radius updates were unsupported. A production robust policy following this contract would abstain; the profitable nominal comparator is not an authorized automatic fallback. A later study must resolve the previously described sampler-support contract before claiming robust performance. Similarly, the exact-zero relative-gap rule rejected 1,128 otherwise optimal solves with very small absolute discrepancies. It can remain a deliberate strict policy, but any alternative tolerance must be justified and frozen before another evaluation, not selected to improve these results. No unsupported rho, bands or confidence guarantee should enter production.

6. **Historical universe, corporate actions and availability — material to generalization.** Only the original85 cohort produced trades; restored307 was entirely refused for this replay. Current snapshot reconstruction and explicit action inventories do not establish complete point-in-time investability, delisted-name coverage or a historically complete universe. Production needs governed as-traded prices and contract adjustments at its actual decision clock. To claim performance across all392 names, a future historical study would also need adequate verified history for those names; the present 131 trades cannot supply that claim. Live use must preserve saved feature ordering, transformations, units and model/calibration identities.

7. **Expiry and publication clocks — measured distinction.** All 131 trades had exactly 31 calendar days to nominal expiry, and every saved settlement-price date equaled expiry. None used a later date's price as an expiry-price substitute. The confirming bar arrived three calendar days later for124 outcomes and four days later for7; this evidence supports the configured historical confirmation convention, not a verified vendor publication timestamp. Production should consume actual available-at timestamps and broker exercise/settlement records. It should retain the corresponding forecast horizon, contract expiry and calibration-maturity checks, rather than assuming every label is available immediately at expiry.

Operationally, this was one pipeline execution, not a scheduled production service. The existing dskit.production layer already provides release/serve, execution, guards and reconciliation seams; inventory and reuse those before adding infrastructure. Those controls were not exercised here, and CondorExpirySettle.serving_effect explicitly returns forbidden: its synthetic expiry accounting cannot be deployed as a live settlement handler. Real order/position reconciliation, restart behavior, stale-data handling and execution deadlines still require validation in the eventual integration.

The underlying mechanics are therefore reusable, but **the existing P&L should not be called production-realistic or deployable**. A useful next qualification packet would first freeze executable timing and the hold-to-expiry lifecycle, establish the evidence needed for fills and prices, and resolve supported calibration and any numerical-policy changes. It would then evaluate that frozen policy chronologically, with account constraints if account performance is the objective. This identifies requirements; it does not authorize new acquisition, model fits, additional backtests, protected2026 access or live execution.

Private evidence for the new counts: condor-production-realism-audit-v1.json under the existing audit root. Its source hash matches the completed run. The previously reviewed results, model inventory and recovery receipts remain unchanged.

## Reproducibility, recovery and next stage

Execution candidate: ada4169b2c307a5585444193a73ed1344cc11b74.
Run hash: 793013c0e7ab77e4802d9e24927a91b41d2545e08ddef3424e22bcc2c928b238.
Config file SHA256: ecd57724a0bd66918568f351bfc0036feff4b845971908ee5923ca0968b7776f.
Launch: 2026-10-09T10:44:26Z. All ten stages completed once, exit 0.

The recorded operation was /home/russell/dskit/.venv/bin/python -m dskit.pipeline run children/index_options/configs/run-equity-condor-robust-backtest.json --asof 2025-12-31 from /home/russell/wt/robust-condor-backtest, with that checkout and its children/index_options directory on PYTHONPATH. This is a reproducibility record, not an instruction to dispatch another run.

Private run:
 /home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-v1/equity-condor-robust-backtest-2025-12-31-793013c0

Private audit:
 /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008

Verified second copy:
 /home/russell/data/backups/index_options/production-universe-rerun-20261008/condor-replay-complete-v1

The second copy contains all 22 run files, the full stdout log, launch/lock receipts, dependency versions and source-ada4169b.tar. The audit's condor-replay-complete-v1-recovery.json verifies every run-file hash and recovers all 131 outcomes with original run, onboarding and derived-input roots denied. Input backups are condor-replay-inputs-v5 and condor-replay-onboard-v5 beside it. Recovery is a second copy on the same disk, not off-device disaster recovery; dependency pins do not replace the installed runtime. No artifacts were deleted.

For inspection, recover the run tree and verify hashes against the receipt; read result.json and artifacts/diagnostics/evidence.json. Do not rerun the pipeline merely to recover results. All model inference locations and preprocessing/load instructions remain in the linked forecasting inventory and memo.

The authorized first research replay is complete. It establishes working end-to-end execution and accounting, but does not establish robust-strategy performance. A subsequent iteration needs a prospectively reviewed sampler contract that covers its intended history, or legitimately eligible prior template evidence satisfying the existing contract; it also needs an explicit numerical certificate policy before another run. Preserve this result when revising either. Do not silently drop dates, enlarge the rho search or tune thresholds against these profits.

Independent production qualification would additionally require appropriate untouched evidence and executable timing/fill, action and instrument assumptions. RunReport correctly marks deployment checking not evaluable. No deployment, live trading, new fitting or 2026 qualification is authorized. The owner subsequently requested wrap on2026-10-09, authorizing reviewed integration and publication. This changes the delivery authorization, not the experiment or deployment scope.

## Reporting closure

Report candidate 6b82e0c7 passed independent accounting/provenance and math/calibration/claims reviews with zero Critical, Major, Minor or Nit findings. The audit retains condor-report-accounting-review-v2.txt, condor-report-luna-review-v2.txt, condor-report-checks-v2.json and condor-report-final-lock-v1.json. A draft ticker-refusal allocation error was corrected across the entire appendix before publication; earlier drafts and findings remain private. No runtime, configuration, model, calibration rule or replay outcome changed. This closure paragraph is an evidence-only append to the reviewed candidate.
