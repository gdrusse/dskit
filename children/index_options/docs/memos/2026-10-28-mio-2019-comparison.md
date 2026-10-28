# 2019 MIO comparison: broad and limited universes

## TL;DR

All eight fixed weekly MIO runs completed and passed independent replay checks. Broad-universe W1 plus shock rows produced the largest observed PnL, **$89,497.05**, with a **61.89% maximum marked drawdown**; the limited nominal run produced **$42,278.21** with **76.57% drawdown**. This is a retrospective 2019 development comparison with concentrated returns, incomplete stress coverage and no formal winner or promotion decision.

## Execution contract

The owner authorized all of 2019, the selected model-zoo output, both broad and limited universes, four existing MIO formulations, candidate/trade/PnL exports, hindsight diagnostics and existing stress tests. Path A0003 and A0008 remain binding: 2019 is development evidence; the saved zoo recipe supplies predictions. Path A0005/A0007 promotion gates were not evaluated. Path and TDD are unchanged; the owner will update TDD with the exact pins.

**Predictions and universe.** The corrected source is the OI-free 65-input seed-29 MLP64 plus M53 rolling dispersion blend, from the saved H1 and H2 2019 producers. It is not TCN or the superseded seed-11/116-name source. There are 214 declared training names, 212 with usable 2019 forecasts, and 268,169 matched forecast/context rows. COHR and ECHO lack usable forecasts in both halves; H2 also lacks SMCI. The H1 producer's scored fold trains from 2017-01-03 through 2018-12-31 and validates 2019-01-02–2019-06-28; its warmup is 2018 H2. The H2 producer's scored fold trains through 2019-06-28 and validates 2019-07-01–2019-12-31; its warmup is 2019 H1. Both declare cal_n=82 and settlement purging. The recipe was selected using H1 evidence, so the broad annual comparison is also retrospective. The exact upstream fold JSONL bytes and hashes are captured in upstream-model-pins.json. The M53 blend uses model, calibrated market, pooled and baseline components; rows without a usable blend retain the selected trained MLP fallback. Fallback rows are retained by the configured require_fitted=false.

- Broad cohort: 212 supported names, signal-date calendar DTE 1–60.
- Limited cohort: 36 sparse ticker–DTE-band cells across 21 names. The existing 1,712-row (214 × 8) performance CSV supplies the rule: at least 50 comparable rows in each 2019 half and positive M53 threshold-weighted CRPS improvement against raw market, calibrated market and horizon empirical in both halves. Of 733 coverage-eligible cells, 36 pass this retrospective rule.
- The limited cohort uses both halves' outcomes to choose cells and then reports the same year's MIO results. It is not an independently selected validation sample, verified 2018 eligibility, or a list of demonstrated profitable stocks.

**Calendar and money.** Each method has 53 weekly decision rows and 252 account sessions. Decisions use information available at that close; intended books fill all-or-none on the next session's end-of-day NBBO, then remain in one continuous funded account until expiry. Rejected books are retained and never replaced. Funding is $10,000 initially plus $1,000 every 14 days, totaling $35,000 contributed; commission is $0.65 per contract and multiplier is 100. DTE is measured on the signal date, not fill date.

Selected observations are confined to 2019-01-02 through 2019-12-31. Saved forecast labels and actual trades settle before 2020. Prepared bar/session history begins 2018-10-03. The 2020-03-08 control as-of and protected-end metadata are settlement-tail/control values, not permission to use 2020 market observations. No new 2020+ observations, packets, model fits, HPO, or provider pulls were added; protected 2024–2026 outcomes remain untouched.

**Four fixed formulations per cohort.**

- Nominal: transport radius zero.
- W1: Wasserstein radius 0.03.
- W1 plus shock: radius 0.03; basket moves −10%, −5%, +5%, +10%; decision-time new-selection loss limit 10% of funding, with strictly prior 60-session betas.
- W1 plus component cap: radius 0.03 and 25% component reserve cap. Components are connected groups from strictly prior 60-session returns: at least 20 paired observations; join when absolute correlation is at least 0.6 or correlation is missing/invalid. They are not industry sectors.

All use the existing LP group screen, portfolio/name funding fractions of 1, short-notional fraction 0.2, and 25% funding reserve fraction. Quote filters include |z| ≤ 2.5, relative spread ≤ 0.5 and minimum size 1. Basket betas/components naturally differ between the two universes.

**Actual guard semantics.** HiGHS is configured with absolute objective gap $50, relative gap 0, parity tolerance $0.001, one thread and 900 seconds per solve call. Timeout means no new orders; setup and replay time are additional. The certificate applies only to the LP-screened program: excluded standalone-negative groups can be useful joint hedges. The gap does not bound prediction error or realized losses, and cash with a bound below $50 does not prove there was no opportunity.

The inherited bridge's max_total_contracts=10 is not a weekly-portfolio quantity cap. Weekly quantities follow quote capacities and funding/reserve constraints. A retained limited nominal VFC book contains 82 calls. This corrects the old integration report's cap wording; the report is preserved and the correction is recorded in analysis-design.txt.

Shock rows cover the newly selected batch, exclude existing held books, and are not recomputed after individual fill refusals. Funding/reserve limits are rechecked on surviving fills. Thus the label “shock” is not a guarantee that the filled or accumulated account satisfies a 10% loss ceiling.

## Empirical results

Amounts below are USD after commission. “Extra spread” subtracts another 25% of quoted bid–ask spread × 100 × filled quantity from fixed trades; it is separate from the five scenario repricings. TWR is time-weighted return, which adjusts for deposit timing. TWR and drawdown are fractions in CSVs and percentages here. Fill rates count ticker/expiry books, not contracts.

| Cohort | Method | PnL ($) | Extra-spread PnL ($) | TWR | Max drawdown | Filled / selected books | Contracts | Timeouts |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| limited36 | Nominal | 42,278.21 | 39,325.21 | 60.08% | -76.57% | 40 / 149 | 992 | 0 |
| limited36 | W1 0.03 | 607.90 | 99.40 | 20.47% | -24.54% | 10 / 20 | 154 | 0 |
| limited36 | W1 + shock | 196.55 | -289.70 | 14.46% | -25.98% | 10 / 19 | 153 | 0 |
| limited36 | W1 + component | -6,156.25 | -6,723.50 | -4.08% | -38.63% | 9 / 16 | 165 | 0 |
| broad212 | Nominal | 20,446.21 | 18,784.21 | 16.67% | -62.80% | 61 / 310 | 1172 | 0 |
| broad212 | W1 0.03 | 36,141.65 | 31,795.65 | 332.54% | -79.50% | 19 / 101 | 1119 | 0 |
| broad212 | W1 + shock | 89,497.05 | 79,935.80 | 747.15% | -61.89% | 50 / 202 | 2683 | 2 |
| broad212 | W1 + component | 17,224.10 | 14,575.35 | 169.45% | -73.54% | 25 / 98 | 906 | 0 |

The final-account identity is PnL = final equity − contributions = sum of settled book PnL. For broad shock: $124,497.05 − $35,000.00 = $89,497.05. This dollar profit divided by contributions is not TWR. All eight accounts reconcile; no book remains unsettled.

Broad shock has the largest observed PnL and the smallest broad-cohort marked drawdown, but 61.89% remains substantial. Limited W1 and limited shock trade much less and have lower drawdown, with only $607.90 and $196.55 PnL. Limited shock becomes −$289.70 under the extra-spread diagnostic. These are descriptions of eight correlated comparisons, not a statistical winner: no preregistered primary ranking metric, error budget, allocation rule, confidence interval or independence assumption was established.

**Execution gaps.** Broad shock skipped new orders after two timeouts, 2019-05-13 and 2019-08-19. All other forward methods have zero observed timeouts; this does not prove future timeouts cannot occur. Broad shock carries one book mark on 2019-01-24 because a leg was unquoted; all other method/session carried-mark counts are zero. This can affect marked return/drawdown, although terminal PnL reconciles. Missing quotes, insufficient displayed size and repriced shared caps explain the retained rejected orders.

**Concentration and model fallback.** Broad nominal's TMO book (2019-10-21 signal, 2019-12-20 expiry) earned $20,445.90, almost its entire $20,446.21 annual PnL. Broad W1's TTD book (2019-04-08 signal) earned $53,860.50; broad shock's W book (2019-01-22 signal) earned $55,794.90. Limited nominal's 82-call VFC book earned $24,628.70, and its JPM book earned $16,595.60. These are observed concentrations, not additional selection rules.

| Cohort | Method | M53-fitted books / contracts | M53-fitted PnL ($) | Fallback books / contracts | Fallback PnL ($) |
|---|---|---:|---:|---:|---:|
| limited36 | Nominal | 28 / 586 | 7,608.11 | 12 / 406 | 34,670.10 |
| limited36 | W1 0.03 | 6 / 29 | 4,328.15 | 4 / 125 | -3,720.25 |
| limited36 | W1 + shock | 6 / 32 | 3,780.20 | 4 / 121 | -3,583.65 |
| limited36 | W1 + component | 5 / 41 | -2,321.65 | 4 / 124 | -3,834.60 |
| broad212 | Nominal | 37 / 689 | -3,430.85 | 24 / 483 | 23,877.06 |
| broad212 | W1 0.03 | 9 / 445 | 51,258.75 | 10 / 674 | -15,117.10 |
| broad212 | W1 + shock | 27 / 579 | 130,079.65 | 23 / 2104 | -40,582.60 |
| broad212 | W1 + component | 12 / 237 | 37,868.95 | 13 / 669 | -20,644.85 |

The split uses each filled book's M53 fitted flag. Fallback is the selected trained base model; its PnL must not be attributed to learned M53 blending.

## Candidate, trade and known-answer CSVs

Each cohort has the same export structure. Dates, ticker, DTE, expiry, group/candidate IDs and method labels preserve joins.

- candidate-options.csv: the full prepared signed-contract menu, including options later excluded by the LP screen; each method's selected and filled quantities, individual-leg realized PnL, next-session availability and nominal one-contract forecast value.
- candidate-group-methods.csv: method-level LP eligibility, selected/filled book flags, robust group/portfolio objective and certificate fields. candidate-refusals.csv retains pre-prepare refusals.
- orders-and-fills.csv: a convenience join for every selected leg, including unfilled orders, model/optimizer values, available-option count, realized leg PnL and best single-contract known outcome for the same date/ticker/expiry. Limited-only conditional portfolio fields are supplementary; broad conditional values are blank and explicitly marked omitted.
- trades.csv and trade-legs.csv: filled books and legs. The latter repeats parent-book PnL per leg; never sum that repeated column. orders-and-fills.csv and candidate-options.csv contain individual-leg PnL.
- decisions.csv, rejected-orders.csv and daily-account.csv: every weekly decision, fill refusal and daily account state. pnl-by-ticker-dte.csv aggregates filled contracts and individual-leg PnL.
- best-known-single-contract.csv: best observed one-contract outcome within each prepared group, without asserting portfolio feasibility.
- hindsight-decisions.csv and hindsight-choices.csv: completed limited-cohort conditional portfolio diagnostics. Broad .partial.csv files preserve interrupted optional work and are excluded from the convenience joins. Each completed retained program/result JSON is hashed.

The limited export contains 178,578 signed-contract rows, 6,004 group/method rows, 184 refusals and 1,498 single-contract maxima. The broad export contains 5,541,792 signed-contract rows, 221,248 group/method rows, 619 refusals and 54,954 single-contract maxima. Luna independently reconciled each completed export with the forward results. Use a CSV query tool for the full broad menu; it exceeds a single Excel worksheet's row capacity.

Nominal unit value, robust group value and robust portfolio value answer different questions. Group and portfolio values repeat across leg rows and must not be added again. “Possible options” means the configured, filtered signed-contract menu, not every exchange-listed instrument or every possible strategy combination.

**Meaning of the known answer.** The requested primary known answer ranks prepared single contracts by actual expiry PnL after next-session price and commission. For candidate j, define q_j as +1 for a long or −1 for a short, I_j as its expiry intrinsic value per underlying unit, and p_j as its next-session fill price per unit. Then one-contract profit is π_j = q_j × 100 × (I_j − p_j) − $0.65; the exported best value is max_j π_j over candidates with valid quotes, capacity, basis and settlement. It is an outcome comparison, not a jointly feasible portfolio or an executable forecast.

The supplementary portfolio diagnostic instead freezes that method's actual forward funding, existing reserves and original LP survivors. It substitutes known terminal settlement, next-session prices, commissions and the smaller of signal/fill sizes; unavailable legs have zero capacity. It retains both signal/fill reserve constraints and original signal shock rows. This is a stricter hypothetical feasible set, not a bound on the actual partial-fill portfolio or an unrestricted oracle. Weekly values must not be summed into an achievable annual return.

Limited36 completed 212 method/week rows: 206 certified within $50 absolute gap, four no-entry rows and two without a program. The largest certified gap was $49.99385795; 1,145 chosen-leg rows are retained.

The broad optional portfolio pass was stopped at owner direction after 32 completed diagnostic solves (30 certified, two uncertified), through 2019-02-19. Its partial CSVs and all 32 result JSONs remain preserved in scope-correction.json. They do not represent full-year coverage, and their numerical values are intentionally absent from orders-and-fills.csv. The per-option known-answer export was already complete for both cohorts. Adding these repeated constrained portfolio optimizations was unnecessary for the requested best-option comparison and delayed stress testing; the remaining optional solves were deliberately not run.

The retained supplementary hindsight helper calls the existing TransportPortfolioSolve node directly using pinned JSON program/result files; it is the disclosed exception to ordinary JSON-pipeline orchestration. No new optimizer engine was built. The same $50/900-second guard applies; uncertified incumbents, bounds and refusals remain visible.

## Existing stress tests

The repository's OptionScenarioReprice runs separately for actual filled books at entry and while held on weekly decision dates. Every book/scenario has its exact config, run identity or explicit refusal. Five configured cases are unchanged; underlying −5%/IV +5 percentage points/one day; −12%/+15 points/one day; +10%/−5 points/one day; and expiry with underlying −15%. The expiry case uses each book's actual remaining DTE, correcting the template's fixed 30-day value.

Pricing uses same-date quoted IV, zero interest and spot as the forward proxy. Missing or invalid IV, basis mismatches and the existing numerical envelope refuse instead of being filled with invented inputs. Model mark change, full scenario value and fill-credit-anchored PnL are separate. Commission is included once in fill credit. Existing structure_payoff supplies the expiry terminal PnL even when Black-76 baseline repricing refuses.

Both stress queues completed at 17:34:35 UTC on 2026-10-28. Across 5,645 book/date/scenario cases, 4,496 priced and 1,149 explicitly refused. Limited36 priced 1,208/1,600 cases (301/345 entry; 907/1,255 weekly-held). Broad212 priced 3,288/4,045 (693/775 entry; 2,595/3,270 weekly-held). These are processing-completion counts, not a risk acceptance test.

Refusals were entirely retained preflight checks: limited36 had 232 numerical-envelope and 160 missing/invalid-IV cases; broad212 had 582 numerical-envelope and 175 missing/invalid-IV cases. No baseline or scenario values were imputed.

For each method there are 212 common-horizon weekly rows (53 dates × four scenarios). “Active” means at least one held book; the complete-active numerator requires every held book to price. Cash-only rows are shown separately and excluded from that numerator.

| Cohort | Method | Complete / active weekly rows | Cash-only rows | Largest priced weekly model drop ($) | Date / scenario |
|---|---|---:|---:|---:|---|
| limited36 | Nominal | 96 / 172 | 40 | -21,810.29 | 2019-04-29 / down_5_vol_up_5 |
| limited36 | W1 0.03 | 16 / 68 | 144 | -1,818.97 | 2019-01-14 / down_12_vol_up_15 |
| limited36 | W1 + shock | 30 / 68 | 144 | -4,106.23 | 2019-02-04 / down_12_vol_up_15 |
| limited36 | W1 + component | 36 / 76 | 136 | -4,403.74 | 2019-10-21 / down_5_vol_up_5 |
| broad212 | Nominal | 75 / 196 | 16 | -7,850.61 | 2019-11-25 / down_12_vol_up_15 |
| broad212 | W1 0.03 | 107 / 168 | 44 | -134,332.21 | 2019-06-17 / down_12_vol_up_15 |
| broad212 | W1 + shock | 108 / 192 | 20 | -116,535.08 | 2019-06-17 / down_12_vol_up_15 |
| broad212 | W1 + component | 120 / 184 | 28 | -58,964.71 | 2019-06-17 / down_12_vol_up_15 |

For example, broad shock on 2019-06-17 had three held books and complete pricing under the −12% underlying/+15-point IV/one-day shock: model baseline equity $250,708.25 fell to $134,173.17, a $116,535.08 model change. Its observed NBBO equity was $251,622.80; the two baselines are not interchangeable. This is a hypothetical marked loss at that date, not an additional realized annual loss.

Incomplete active weeks can conceal larger losses, so the displayed largest priced drops are not worst-case bounds and cannot rank risk reliably. The “shock” optimization label does not overcome the accumulated-book and fill-subset limitations described above.

Weekly account totals are present only for the four common-horizon scenarios when every held book prices. Cash-only rows are valid but do not demonstrate stress coverage of risky positions. Different-expiry terminal stresses remain per-book. These scenarios have no assigned probabilities, do not establish a maximum loss, and do not simulate American assignment, financing, borrowing, broker margin, forced liquidation or stressed fill liquidity.

## Implementation evidence and incomplete/superseded work

The corrected run used reviewed runtime commit 64cb99b5aacecab8f2b579b3dc3dd09b17087b73, with the small saved-two-halves receipt compatibility change at 70b3cc09. The four existing formulations, weekly engine and stress node were reused. Narrow checks passed: 19 packet tests and the exact child manifest. Both frozen-runtime Luna review lenses closed with no Critical/Major findings. The old integration cap sentence is corrected above and in the retained follow-up.

The earlier 116-name/seed-11 service was stopped and its partial output retained as superseded; it is excluded from all eight comparison rows. The first localfiles source used a recursive glob that omitted 13 root-level inputs; its 222-file snapshot is retained but unused. The corrected source includes all 235 files. The earlier supplemental combined test invocation had five subprocess import failures from another checkout; those were not established as pre-existing and are not reported as passing.

Forward comparisons and independent replay finished at approximately 14:18 UTC on 2026-10-28, roughly eleven hours after the corrected worker began. Solves, preparation and replay/reconstruction explain the elapsed time; this missed the intended morning completion. The longer elapsed time did not represent additional model training. The optional broad portfolio-hindsight queue was stopped at 17:21 UTC under the owner's scope correction. The first stress execution then rejected uppercase ticker labels in JSON pipeline series names before any stress node ran. Only those labels were lowercased; all 5,645 configurations were compared with their preserved originals, and all 4,496 eligible documents passed schema loading. Original configs, failed attempt receipts and exact corrected hashes are retained. The frozen analysis/stress helpers and runtime code were unchanged.

The packaging guard verified completed forward and stress receipts, exact run identities, candidate/stress CSV hashes and all eight independently replayed results. It reconciled individual-leg realized PnL with account totals before building both archives, then checked every ZIP CRC. The final delivery gate and its review/test evidence are recorded in the result pins; the bundles preserve their earlier review snapshot and are not silently rewritten after final review.

Final evidence candidate 782f1751e51c212f3fa500a1f983c1a826ed1a4a received two fresh independent GPT-6 Luna reviews: correctness/authority and tests/integration, each with zero unresolved Critical, Major, Minor or Nit findings. Both reviewed the same immutable candidate. The exact-manifest check and four documentation checks passed (5 tests); no full suite was run. Before these closure-only evidence updates, all reviewed file identities were checked unchanged. Final reports are pinned separately from the earlier immutable bundles.

No 2018 eligibility verification, fresh-year MIO test, new hyperparameter tuning, TDD edit, production promotion or live trading was performed. The 2019 comparisons are exposed development evidence. Next: review concentration, filled-subset shock semantics and stress gaps together, then decide the next frozen cohort/formulation and authorized window before opening more data.

## Reproducibility and handoff

The small repository outputs are this memo, the eight-row comparison CSV and configs/mio-2019-results-pins.json. The original five source/study JSONs remain pinned unchanged. Full local artifacts are under /home/russell/data/index_options/mio-broad-2019-20261027; large CSVs and raw data are not committed.

- limited36: document b8d194bacc9fbf96de19be7145c90697580277d24ecf3f40245ba5e4ad2958f6; run e72da8427bc9274a0f35a5effee75e1789944a47cbcac9b08ae58431b0b886f9; close receipt bd5091ad6164f9613a4bb7586ce546ffa4bb50ecd0f5e55fd04c942a13de7c32.
- broad212: document bba78d0995e7ade4999a52f845b1f109013f3905ae2688c3dad5cede620e6bbe; run e216234597328a31fa8ec915cd60422172796f1cc98ad1c8f11a9b20f9c0547c; close receipt 63cb8c2d826cbd581691cd01b9b8b77f492d0135b9c55d7257cd6d59abb3c95a.

Runtime and all stress executions used commit 64cb99b5aacecab8f2b579b3dc3dd09b17087b73. The final documentation candidate integrates upstream ec5028184c976fac3a403e497537d336a6f0add3 after executions completed; historical JSONs still pin their original execution environment. Reproduction must use the pinned historical code, not reinterpret them against a newer checkout.

The final pin JSON links exact source manifests, upstream fit/score/M53 configs, execution and comparison receipts, postprocess plan/helper hashes, per-stage receipts, review reports and delivery hashes. Original producer receipts attest completed nodes/configuration but do not attest the original saved output bytes; the currently consumed bytes were hashed during preparation.

mio-2019-csvs.zip contains the detailed CSVs and column guide. mio-2019-reproducibility.zip contains the executed/generated JSONs, helper scripts, source-selection evidence, run receipts and retained reviews. Raw market/model Parquet and SQLite stay in their pinned WSL/onboarding stores. artifact-index.json hashes each bundled file. Downloads remain available on the private Tailnet viewer, http://gibson-deskop.tail27a0c8.ts.net/ .

Packaged downloads: [CSV bundle](http://gibson-deskop.tail27a0c8.ts.net/download/csvs.zip) (191,722,367 bytes) and [JSON/run evidence bundle](http://gibson-deskop.tail27a0c8.ts.net/download/reproducibility.zip) (341,452,171 bytes). [Memo download](http://gibson-deskop.tail27a0c8.ts.net/download/memo.md) is a separate final document. The bundle hashes and file index are in the result pins; final review/push receipts follow the frozen bundle snapshot. The private viewer remains available to the owner's Tailnet.
