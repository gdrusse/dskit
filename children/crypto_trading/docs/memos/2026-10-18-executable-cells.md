# Complete venue/cell execution follow-up — October 18, 2026

## TL;DR

**Continue with a small, frozen fresh-book replication; do not claim a profitable strategy or run another tuning sweep.** The broad follow-up tests all 77 saved Kalshi methods/settings and all 20 native Polymarket BTC models, rather than only prior favorites. Eight adequately sampled Polymarket model/cells have positive primary scenario averages, but none passes the primary search-adjusted test. Apparent alternate-scenario statistical successes all contain zero observed losses and fail a separate rare-loss uncertainty diagnostic.

The most useful new research reserve is **Polymarket BTC / 11 minutes / UP midpoint 80–90% / Huber method10**. It has a positive fresh-book cost scenario and reasonable loss coverage, but remains retrospectively selected and unconfirmed. The former beta02 / BTC5m80–90 favorite produces just two entries under the primary cost rule. Kalshi retains 26 cells with some positive two-period cost scenarios; its historical candle data cannot establish delayed executable fills.

## Execution contract and complete coverage

The owner requested actual research across all plausible cells, both venues and both sides, including profitability. Base commit: `5b43b5e178720561b0ff9c64c9354fec98f8a12b`. Work ran in the isolated WSL checkout `crypto-executable-cells-20261018`; no toolkit mechanism, live order, automation, protected January 2027 protocol, or owner Path was changed.

The inclusive rule retains every existing lead/decile region, regardless of prior support or sample size. Leads are **2, 5, 8, 11 and14 minutes**; bins are ten-point ranges of the decision-time YES/UP probability. This is exhaustive for that declared grid, not every continuous time, threshold, size or imaginable strategy. Both purchased sides remain separate ledger entries; they are not independent outcomes.

- **Kalshi BTC and ETH:** all100 regions, 77 methods/settings, two historical periods, two sides, three order sizes: **92,400 model cost-scenario rows plus2,400 matched raw/logistic control rows**. The matching **15,400 prediction-score rows** preserve original model/raw/calibrated losses and probabilities.
- **Native Polymarket BTC:** all50 regions, 20 unchanged models, raw and calibrated controls, plus an unconditional diagnostic. Twenty attested JSON runs produce **30,383 UP forecasts /6,446 contracts /72 days**; 1,000 model/cell score rows, 3,000 freshness score rows and **41,400 execution-scenario rows**.
- The live inventory also contains **92 ETH,92 SOL and92 XRP markets**, with roughly22.5 hours of books and **zero retained terminal outcomes** per asset. Their150 regions/300side-cells are explicitly blocked, not omitted or assigned BTC labels/models.
- The [600-entry coverage ledger](2026-10-18-executable-cells-coverage-ledger.csv) includes every tested, empty, sparse and blocked side/cell, original support/selection identities, and minimum missing data. Zero signals are not evidence that a cell cannot be profitable.

The original six Kalshi selections, eleven persistent-gap leads, all remaining reserves and failed cells are included. No top-k filter was used. Models and rules were specified before new net-return aggregation; the protocol was later corrected to conservatively count all model families and newly inventoried blocked assets. All these dates were previously researched: this is retrospective follow-up, never untouched confirmation.

## Training, chronology and native identities

Kalshi probability forecasts were reused without refitting. Warm-up is February19–April19,2026; validation is May2–July31. Original correction-layer cutoffs and the disclosed July1 GRU backbone update remain intact. The [training explanation](../explanations/market-model-training-and-validation.md), [aligned57 memo](2026-10-18-warmup20-aligned-methods.md) and [tuning20 memo](2026-10-18-pocket-tuning.md) retain their training losses and selection history.

Polymarket uses the same20 existing estimator/feature/anchoring configurations, with the same independent lead-logistic comparator. The frozen May2 fit uses **60,226 forecasts /12,068 contracts**, beginning December15,2025 and ending April19,2026. No May–October label enters fitting. End+24hours is still an assumed label-availability embargo; actual historical publication times are not established. The [original training-loss export](2026-10-18-polymarket-predictability-training.csv) remains applicable because training inputs and parameters are exactly unchanged; eleven focused checks verify that identity, cutoffs and outputs.

The transfer changes the probability measurement from historical scalar prices to explicitly computed native UP bid/ask midpoints. It does not establish identical semantics between those inputs. Age-sensitive models also face this distribution change. The [freshness comparisons](2026-10-18-executable-cells-poly-prediction-freshness.csv) compare model, raw and calibrated probabilities on the exact same rows under2/10/60-second age limits.

Native Polymarket token strings and dated outcome metadata determine UP/DOWN membership and settlement. The known rounded numeric token column is never used. No Kalshi oracle, spot label, ETH label or GRU prediction is substituted. Native book metadata contains6,911 settled BTC contracts; the archive provides rows for6,483, with further eligibility losses before the6,446-contract prediction panel. Missing hours, token coverage, invalid books and age restrictions remain distinct from failed predictions.

## What the execution scenarios mean

At decision instant I, use the latest locally emitted full book available by I, and a fixed model probability. Buy a side only if its predicted probability exceeds its **decision-time depth-weighted ask plus fee**. Selection never uses settlement or the later entry price.

The primary scenario is **10 shares, one-second delay, and at most two seconds of both exchange-update age and local-emission age**. Other declared scenarios vary size1/10/100, delay1/5seconds and age2/10/60seconds. The later book must be available by I+delay; prices consume the purchased token's own ask depth. Insufficient depth, invalid/crossed/empty books and stale observations are excluded and counted. Adverse price moves after the decision are retained; they are not removed by a hindsight profitability filter.

The source coalesces updates to at most one full-book row per token per second. `asof_ts_ms` is the local emission/availability timestamp; it is not proven individual message receipt. `ts` is the last exchange update. Between-snapshot cancellations, competition, actual matching and queue position are missing. Consequently, these are **displayed-depth scenarios, not observed fills or realized trading profit**. No maker/limit-order profits are asserted. Forecast and signal counts in the exports show how much disappears before a modeled entry; excluded submissions are not silently treated as fills.

Polymarket fee calculations reuse DSKit's quadratic mechanic, with the acquired events'0.07 rate and aggregate five-decimal rounding. Current [official fee documentation](https://docs.polymarket.com/trading/fees) corroborates that formula and precision. Event fee metadata was acquired later; immutable effective-at-trade fee history and per-match rounding are not fully established. Fees exclude hypothetical rebates. Additional adverse slippage of0.5 and1cent per share is reported separately.

For one share, the scenario return is **native payout − delayed ask cost − fee**. For the same row, the modeled extra-cost allowance is **model probability − delayed ask cost − fee**. The former uses outcomes; the latter uses the previously fitted forecast. They must not be interchanged.

Kalshi uses a separate **historical candle-close quote scenario**, buying YES at its ask or NO at1−YESbid. It applies the existing0.07 quadratic mechanic and cent-ceiling order fee for1/10/100contracts. The [July7 official schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) supports the general rate, but its centicent prose and cent-rounded examples differ; pre-July fee vintages are not established. The retained cent-ceiling is an explicit cost assumption, not a claim of exact historical charges. No candle price is represented as a positive-latency fill, and candle volume is not standing depth.

## Best research reserves and weaker prior favorites

**Polymarket Huber10 / BTC11m / UP80–90:** primary signal/fill eligibility yields **324 contracts over68 days**, with291wins and33losses. Mean UP midpoint is85.071%, model86.909%, entry cost85.586% and fee0.859cent per share. Native UP settles89.815% of these contracts.

The displayed-depth return is therefore **0.898148−0.855859−0.008591 = $0.033699 per share**. Early/late calendar means are3.731/3.081cents across144/180contracts. The day/week standard errors are1.677/1.611cents; unadjusted one-sided p-values are0.0243/0.0314, both adjusted to1.0. This is a useful research lead, not a supported profit claim.

The same row's **forecast-based** cost allowance is only **0.869090−0.855859−0.008591 = $0.004640**, about0.464cent. The3.37cent historical return cannot be used as a forecast or slippage budget. On all391 UP forecasts with source age≤2seconds in this cell, log-loss gains are0.005757 versus raw and0.000713 versus calibrated market; neither survives the freshness-search correction.

**Polymarket BTC5m / UP90–100:** several fixed residual models deserve a rare-loss audit, not promotion. ARD07 at5seconds/2second age/10shares has271observations, mean scenario return1.380cents, and **271wins/zero losses**. The23 apparent alternate-scenario t-flags all have zero observed losses, across correlated methods06/07/09 and overlapping size/age settings. They represent one concentrated tail hypothesis, not23 confirmations.

A Studentized day/week calculation in an all-win sample mostly measures variation in prices and fees; it does not learn the risk of an unseen losing payout. We preserve those flags in the exports and mark the limitation. Even an independent-contract all-win lower-confidence diagnostic, adjusted over the full scenario family, is negative for every flag. Dependence would make that simplifying diagnostic less reassuring. One or five additional losing payouts and their effect on average return are also shown.

**Former beta02 / BTC5m / UP80–90:** at≤2second source age,517forecasts have raw/model/realized UP probabilities85.138/86.148/85.880%. The mean log-loss gain shrinks to0.000094 versus raw and becomes **−0.000047 versus lead calibration**. The primary execution rule selects just **two UP entries and no DOWN entries**. Stop treating this as the default trading candidate; its old historical scalar-price score gain does not supply a meaningful executable sample.

**Kalshi full map:**288method/side pairs across26regions have positive cent-fee quote scenarios and at least200selected contracts/20days in both historical periods. This includes weaker and previously unmentioned cells, not just ETH11m10–20. Reference24 alone retains five such side/cells: BTC5m90–100YES, BTC5m0–10NO, BTC8m0–10NO, ETH5m90–100YES andETH5m0–10NO. Their validation forecast-based extra-cost allowances are only roughly0.22–0.55cent per share.

The familiar ETH11m10–20 reference24/NO scenario has81warm-up and130validation selections, below the sample floor despite positive averages. One adequately sampled Kalshi validation primary t-flag, aligned26/BTC5m90–100YES, is also **200wins/zero losses** and inherits the same tail problem; it is still a candle-price scenario. The complete [Kalshi score map](2026-10-18-executable-cells-kalshi-prediction-cells.csv) and [cost map](2026-10-18-executable-cells-kalshi-cost-primary.csv) retain every alternative and failure.

## Uncertainty, dependence and selection

The final conservative net family is **600possible side/cells ×99model/control labels =59,400**. It includes incompatible venue/model and blocked combinations rather than shrinking to attractive cells. Across18declared latency/age/size combinations, the family is **1,069,200**. Bonferroni adjustment is `min(1,family × p)` and does not assume independent hypotheses. Require both day and fixed-seven-day cluster checks; sparse cells remain uncertain. Cluster t approximations themselves still require adequate clusters and outcome variation.

The earlier8,800 and39,600 family counts were superseded when the complete model and asset inventories were reconciled. No forecast, selected row or mean return changed. The added zero-loss diagnostic is explicitly a post-computation validity audit, not a retrospectively invented successful selection rule. The original prediction studies' corrections and earlier research exposure remain relevant; this new family cannot erase the history of searches.

Contracts overlap across leads and models. Each individual side/cell has one settlement per selected contract, but summing cells, model profits or nominal sample sizes would double-count opportunities. No combined portfolio return is claimed. The September12 split is a fixed calendar stability diagnostic; it is not another independent holdout. Depth, age, latency and added-cost sensitivity exports expose fragility rather than choosing a new best setting for deployment.

## Exact data blockers and next decisive tests

The [raw inventory](2026-10-18-executable-cells-raw-inventory.json) locates existing Kalshi books, trades, candles, native markets/outcomes and Binance spot/volatility streams. Books are **not missing altogether**: the retained October sample has481contracts per asset and about27,000snapshots per asset. However, all capture times are minute-rounded, no exchange timestamp is retained, and batch acquisition trails capture by a median31seconds/p95of58seconds. These October books do not overlap February–July predictions and do not establish a fresh1second replay of those historical candidates.

Two October13Kalshi book payloads are empty while their manifests declare nonempty hashes; exact paths and both hashes are retained. No new result uses those books. Native Polymarket's1,632hourly input files passed their retained hashes. Raw-provider provenance before earlier scalar-price exports remains incomplete.

For Kalshi, the minimum decisive input is same-window exchange/receipt-stamped bid/ask/depth with native outcomes and fee vintages, or a longer separately admitted period with causal model/feature state. For Polymarket ETH/SOL/XRP, obtain native settled labels/rules and earlier training history before fitting; roughlyone day of pending books cannot support this experiment. Do not substitute BTC or Kalshi labels.

**Continue:** one explicitly frozen, small native-book replication of the Huber10/11m80–90 reserve, with raw and lead-calibrated controls, and a separately declared rare-loss stress test of5m90–100. First determine whether an admissible uninspected interval exists; none is claimed here. **Stop:** more tuning on these reused dates, default promotion of beta02/5m80–90, and profit claims based on candle/scalar gaps or all-win t-statistics. If fresh-book prediction advantage or modeled cost allowance fails in the next fixed test, close that hypothesis rather than replacing the cell after seeing outcomes.

## Reproducibility and handoff

All result cells are remotely accessible: [coverage](2026-10-18-executable-cells-coverage-ledger.csv), [Polymarket prediction](2026-10-18-executable-cells-poly-prediction-cells.csv), [freshness](2026-10-18-executable-cells-poly-prediction-freshness.csv), [primary execution](2026-10-18-executable-cells-poly-execution-primary.csv), [all execution scenarios](2026-10-18-executable-cells-poly-execution-scenarios.csv), [Kalshi prediction](2026-10-18-executable-cells-kalshi-prediction-cells.csv), [primary costs](2026-10-18-executable-cells-kalshi-cost-primary.csv), and [all cost scenarios](2026-10-18-executable-cells-kalshi-cost-scenarios.csv). Large CSVs may require GitHub's raw/download view.

The [evidence packet](2026-10-18-executable-cells-evidence.json) retains procedure text, source/output hashes, all20run configurations and attestations, all154Kalshi forecast-input hashes, the protocol, failures and review reports. Local full artifacts reside at `/home/russell/data/crypto_trading/research/executable-cells-20261018`. Eleven focused research checks pass; no full test suite ran. Initial connector/run-path failures and a temporary WSL outage were recovered; an unused duplicate source registration is disclosed. Independent review and publication status are recorded in the evidence packet before closure.


### Review closure and retained limitation

Two independent GPT-6 Astra lenses reviewed immutable candidate `c909ef6aea959e10f8fb15b25bf8c26e30649044`, with zero unresolved Critical/Major findings. The evidence packet retains both complete reports, reviewer limits, checked identities and the Minor backlog. All20 run attestations, all1,632 native file hashes and all154 Kalshi forecast-input hashes were independently checked; focused research and documentation tests passed.

**Deferred Minor COR-1:** execution eligibility checks the purchased token's freshness but omits an explicit second check on the UP book supplying the probability and bin. Exhaustive independent replay found **no selected model, raw-market or calibrated-control entry affected in any of the18 scenarios**. One unconditional DOWN diagnostic observation at8minutes has a2.733second UP source age despite passing the2second purchased-token rule; it enters three size aggregates. Those diagnostic aggregates retain this disclosed exception. No strategy conclusion relies on them. Before applying this procedure to new data, retain the UP source age in the join, gate both inputs and add the cross-leg regression test; that behavioral change requires fresh review. The published analysis/configuration/result blobs remain the reviewed candidate's versions.
