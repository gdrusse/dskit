# Complete candidate inventory: Kalshi evidence and Polymarket follow-up

## TL;DR

There are multiple historical leads beyond ETH / 11m / YES 10–20%: six previously selected regions retain some same-model score support across warm-up and validation, and eleven additional regions show a persistent mean-probability gap of at least one percentage point. A deliberately broad scan finds some two-period model support in 70 of 100 regions, but **no adequately sampled region passes both full-scan day and week uncertainty checks**; none of this inventory establishes after-fee profitability. Polymarket belongs in the research scope as a separate, untested venue using the same modeling framework and its own prices, labels and costs.

## Execution contract and coverage

Requested October 18, 2026: scan all attractive candidates and update remotely accessible documentation; explain why existing results cover Kalshi rather than Polymarket. Base commit: 8eb65b220e9004f18225c8307416837b270954db.

This is a read-only synthesis of completed results, not a new fitted-model or trading experiment. It supersedes the overly narrow description of ETH11m10–20 as our only lead. It preserves previous selection locks and original uncertainty results; it does not retrospectively designate any new candidate as preselected.

The scan covers **57 aligned methods plus 20 tuning settings**, each in two periods: **15,400 model/period/cell rows**, joined into **7,700 method/cell pairs**, across all **100 unique regions** (2 assets × 5 leads × 10 raw-market probability bins). The aligned 57 already re-evaluates the earlier 37 experiments, so earlier unaligned recommendations are not counted again. The later attribution study adds controls and diagnostics, not another independent population of cells.

- Warm-up: February 19–April 19, 2026; 52,999 forecasts / 11,213 contracts.
- Validation: May 2–July 31, 2026; 84,728 forecasts / 17,158 contracts.
- All dates are UTC. Both periods had already been examined in earlier research.
- Warm-up means later predictions relative to each causal fit, not in-sample training loss. The [training explanation](../explanations/market-model-training-and-validation.md) gives exact fitting windows and the July GRU update.
- Every within-cell comparison uses the same contracts. Rows at different leads can share a settlement; models and settings are correlated.

No new fitting, acquisition, protected-data evaluation, order simulation, fee calculation or live action was performed. Public Polymarket rule and fee documentation was checked for venue-transfer requirements; it was not used to measure prediction performance.

## How to read “attractive”

This catalog is deliberately inclusive. Its descriptive rules were written in the retained protocol before aggregation, after the historical results were already known:

1. **Adequate sample:** at least 200 contracts and 20 days in each period. Seventy-five of the 100 cells satisfy this.
2. **Two-period score support:** the same model has lower log loss and non-worse Brier than raw market in both periods. Seventy cells have at least one such model (2,311 method/cell pairs). Tiny improvements count; this is not a significance test or a practical-profit threshold.
3. **Beyond simple calibration:** additionally improve log loss and non-worsen Brier versus the fitted logistic-market comparator in both periods. Fifty-five cells have some such model (1,326 pairs). This comparator is not an all-purpose best calibration benchmark.
4. **Persistent mean gap:** actual YES minus average market YES has the same sign and magnitude of at least one percentage point in each period. Eleven cells qualify. The one-point cutoff is an explicit visibility convention, not a statistical or net-profit threshold.
5. **Original selection:** identities come from the two saved warm-up selection locks. Those contain 26 method/cell selections in eight unique regions; 18 selected pairs across six regions retain the two-period score direction above.

“Aligned-24” means original method 24; “tuning-03” means setting 03 of the later tuning study. Numbers in the two namespaces are not interchangeable. Many settings duplicate or closely resemble their parent, so counts are not independent confirmations.

The complete [100-cell inventory](2026-10-18-candidate-inventory-cells.csv) includes every region, its disposition, counts, mean probabilities, fixed method 24/method 02 scores, all supporting method IDs and original-selection IDs. The [evidence record](2026-10-18-candidate-inventory-evidence.json) embeds the scan procedure and full 100-cell records.

## Previously selected regions to retain in the candidate list

These are the six regions where at least one originally selected method retains better raw-market mean scores in both periods. The list is not a profitability ranking.

- **ETH / 11m / YES 10–20%:** 689 validation contracts. Original aligned 22 and tuning 03/06/09/12 retain support. Setting 03 has validation log-loss difference **−0.003899** and Brier difference **−0.001018**. Its raw-market mean improvement remains in both validation halves. This remains the best-developed local hypothesis, but the four tuned models are correlated and none clears the corrected robustness screen. The mean YES gap changes sign between warm-up and validation, so this is not evidence for indiscriminately buying NO.
- **ETH / 14m / YES 70–80%:** 397 contracts. Tuning06/09 improve validation log loss by **0.003023 / 0.002234** respectively and retain improvement in both validation halves. Market mean **73.76%** is already close to actual **73.80%**; the hypothesis concerns individual forecasts inside the cell, not a blanket directional bias.
- **ETH / 11m / YES 20–30%:** 1,022 contracts. Tuning03/12 improve mean validation loss by only **0.000402 / 0.000456**. Tuning03 reverses in the first validation half; tuning12 remains negative but nearly flat there. Keep as a weaker adjacent-cell candidate, without expanding the 10–20 rule after seeing validation.
- **ETH / 5m / YES 20–30%:** 598 contracts. All six originally selected methods (05/22/24/38/44/48) retain two-period raw-score support. Method 24 validation log-loss difference **−0.000939**. Market mean **24.56%**, actual **24.25%**: limited average price gap.
- **BTC / 5m / YES 30–40%:** 480 contracts. Originally selected methods 05 and48 retain support. Method48 validation log-loss difference **−0.001492**; market **34.92%**, actual **34.17%**. Method44 improves validation but its warm-up raw log-loss difference was slightly positive, so it does not satisfy this catalog's two-period screen. The realized-minus-market gap also changes sign across periods.
- **BTC / 2m / YES 10–20%:** 385 contracts. Of originally selected05/44/48, only44 retains two-period support; validation log-loss difference **−0.000547**. Methods05 and48 worsen validation log loss. This is a model-dependent, lower-priority lead, not broad agreement.

None of these regions is a verified trading strategy. The weaker candidates remain visible to prevent a single favored cell from dominating subsequent planning.

## Eleven persistent mean-gap leads

These all have at least 200 contracts and 20 days in both periods. Negative log-loss differences favor the model. The suggested side describes an observed historical mean gap; an actual entry must use information available before settlement and an executable price.

- **BTC / 8m / YES 20-30%:** warm-up market 24.68%, actual 22.35% (595 contracts); validation market 24.74%, actual 23.39% (902). Mean-gap direction suggests checking NO. Method 24 log-loss differences are -0.000874 / -0.001953 (warm-up / validation).
- **BTC / 11m / YES 70-80%:** warm-up market 74.55%, actual 75.87% (605 contracts); validation market 74.75%, actual 76.39% (974). Mean-gap direction suggests checking YES. Method 24 log-loss differences are -0.002252 / -0.002376 (warm-up / validation).
- **ETH / 2m / YES 80-90%:** warm-up market 85.40%, actual 87.09% (302 contracts); validation market 85.36%, actual 86.50% (400). Mean-gap direction suggests checking YES. Method 24 log-loss differences are -0.002607 / -0.000421 (warm-up / validation).
- **ETH / 5m / YES 10-20%:** warm-up market 14.59%, actual 10.84% (526 contracts); validation market 14.68%, actual 12.89% (838). Mean-gap direction suggests checking NO. Method 24 log-loss differences are -0.004057 / -0.002950 (warm-up / validation).
- **ETH / 5m / YES 30-40%:** warm-up market 34.79%, actual 29.02% (317 contracts); validation market 34.47%, actual 30.47% (512). Mean-gap direction suggests checking NO. Method 24 log-loss differences are -0.006901 / -0.000194 (warm-up / validation).
- **ETH / 5m / YES 60-70%:** warm-up market 64.75%, actual 66.88% (314 contracts); validation market 64.88%, actual 66.86% (510). Mean-gap direction suggests checking YES. Method 24 log-loss differences are +0.001763 / +0.000972 (warm-up / validation).
- **ETH / 5m / YES 90-100%:** warm-up market 95.46%, actual 97.22% (1,187 contracts); validation market 95.95%, actual 97.24% (1,881). Mean-gap direction suggests checking YES. Method 24 log-loss differences are -0.002638 / -0.001599 (warm-up / validation).
- **ETH / 8m / YES 50-60%:** warm-up market 54.84%, actual 51.22% (449 contracts); validation market 54.91%, actual 53.03% (743). Mean-gap direction suggests checking NO. Method 24 log-loss differences are -0.001275 / -0.000757 (warm-up / validation).
- **ETH / 8m / YES 90-100%:** warm-up market 93.99%, actual 97.14% (595 contracts); validation market 94.11%, actual 96.09% (921). Mean-gap direction suggests checking YES. Method 24 log-loss differences are -0.003083 / -0.002137 (warm-up / validation).
- **ETH / 11m / YES 70-80%:** warm-up market 74.55%, actual 79.35% (644 contracts); validation market 74.48%, actual 75.65% (1,002). Mean-gap direction suggests checking YES. Method 24 log-loss differences are -0.000441 / -0.000970 (warm-up / validation).
- **ETH / 14m / YES 30-40%:** warm-up market 35.84%, actual 33.83% (807 contracts); validation market 35.52%, actual 33.08% (1,339). Mean-gap direction suggests checking NO. Method 24 log-loss differences are -0.002642 / -0.001241 (warm-up / validation).

The list adds several leads that were absent from the earlier chat summary, notably **BTC8m20–30, BTC11m70–80, ETH2m80–90, ETH5m30–40, ETH5m60–70, ETH8m50–60, ETH11m70–80 and ETH14m30–40**.

Important distinctions:
- ETH5m60–70 has a repeated YES-frequency gap, but method 24 is worse than market in both periods. Some other models improve raw scores; a gap is not evidence that our reference model exploits it.
- ETH11m70–80 has **no model** satisfying this catalog's two-period improvement against the logistic calibration control. Keep it as a calibration-oriented lead.
- ETH5m/8m90–100 remain plausible YES-side hypotheses, but overlap in contracts. Earlier isotonic-method 02 support at ETH5m90–100 fails the weekly-adjusted test.
- ETH5m30–40 has a large repeated mean gap while method 24's validation score improvement is tiny. Mean miscalibration and successful individual forecasting are distinct.

## Earlier selected candidates that weakened

Keep these in the record as negative or mixed evidence, not silent omissions:

- **BTC / 11m / YES 10–20%:** originally selected methods 22/24/38 all worsen validation raw-market log loss. Method 24 difference **+0.000224** despite market mean15.56% versus actual14.42%. Three other methods satisfy the broad two-period raw-score screen, but none also beats the logistic calibration control in both periods. Do not transplant the ETH11m rule to BTC.
- **ETH / 2m / YES 20–30%:** selected methods 24/38 worsen validation log loss (**+0.000676 / +0.000537**). Actual YES29.45% exceeds market24.61%, reversing the warm-up gap. Other retrospective models can look better, but replacing the failed preselected methods from validation would be another selection step.
- **Method 57 at ETH5m90–100:** its validation-only hit followed worse warm-up scores. Preserve it as a failed cross-period replication, not a new winner.
- **BTC14m extreme bins:** saved full-scan adjusted tests produce apparent passes in00–10 and90–100, but validation has only **5 and10 contracts**, with warm-up only7 and6. They fail the sample floor and are excluded from actionable leads. This is why the complete catalog retains counts beside statistical flags.

## The other 51 regions with some two-period model support

To make the scan exhaustive, these regions also satisfy the broad raw-score screen for at least one model. They were not in the original selected-region union or the eleven persistent-gap group. They are exploratory reserves, not 51 additional recommended strategies. The CSV identifies the exact supporting models and their fixed-reference scores.

- **BTC / 2m:** 00-10%, 80-90%, 90-100%.
- **BTC / 5m:** 00-10%, 10-20%, 20-30%, 40-50%, 50-60%, 60-70%, 80-90%, 90-100%.
- **BTC / 8m:** 00-10%, 10-20%, 40-50%, 60-70%, 70-80%, 80-90%, 90-100%.
- **BTC / 11m:** 20-30%, 30-40%, 50-60%, 60-70%, 80-90%.
- **BTC / 14m:** 30-40%, 40-50%, 50-60%, 60-70%, 70-80%.
- **ETH / 2m:** 00-10%, 10-20%, 90-100%.
- **ETH / 5m:** 00-10%, 50-60%, 70-80%, 80-90%.
- **ETH / 8m:** 00-10%, 10-20%, 20-30%, 30-40%, 40-50%, 60-70%, 70-80%, 80-90%.
- **ETH / 11m:** 30-40%, 40-50%, 50-60%, 60-70%, 80-90%.
- **ETH / 14m:** 40-50%, 50-60%, 60-70%.

The remaining 30 cells fail this descriptive support screen; this includes thin cells as well as adequately sampled failures. All 100 remain in the downloadable inventory. There were **zero adequately sampled model/cell pairs** passing both the saved full-scan day and week tests against raw market. Existing full-scan families remain 5,700 aligned or 2,000 tuned comparisons per comparator/period; we did not shrink those denominators to the surviving catalog. The original frozen shortlist tests and the later nine-contrast attribution tests remain separate.

## Why Polymarket must be part of the next research scope

The scientific problem transfers: predict a binary crypto outcome, compare against the venue's own implied probability, and inspect asset/lead/probability regions. The current Kalshi-only result reflects the acquired dataset and completed pipeline runs, not evidence that Polymarket lacks an opportunity.

Earlier project research already considered Polymarket and raised source/access questions. Those are not a scientific reason to omit it from prediction research. DSKIT already has a [Polymarket onboarding pack](../../../../dskit/onboarding/libs/polymarket.py), with Gamma events, derived fee schedules, CLOB books and an archive-hours interface. The missing step here is a verified, aligned Polymarket research dataset and study, not automatically a new connector.

The venue-specific differences matter:

- **Settlement and labels:** the inspected October ETH15m market references Chainlink ETH/USD 60-second TWAP and resolves Up when its specified comparison is greater than or equal. Our Kalshi analysis uses its own strike, settlement source and basis adjustment. Read each dated market's rule; do not copy Kalshi labels or assume historical Polymarket rules were identical. [Official example rules](https://polymarket.com/event/eth-updown-15m-1791068400).
- **Prices and execution:** use Polymarket's own synchronized bid/ask, quote age and available depth. A Kalshi miscalibration pocket may be absent, reversed or located elsewhere on Polymarket. Archive coverage and depth completeness must be checked; a connector's existence does not prove usable historical coverage.
- **Fees:** reuse generic cost plumbing with the correct venue/market/date parameters. The current Polymarket documentation describes market-dependent taker fees and rounding; do not replay the Kalshi fee configuration unchanged or apply today's schedule to historical contracts. [Official fee documentation](https://docs.polymarket.com/trading/fees), checked October 18.
- **Venue identity:** document whether the dataset is Polymarket's international product or Polymarket US; do not mix them. Venue access and permission to trade are separate from a read-only prediction study.

The complete Kalshi catalog is a set of **transfer hypotheses**, not Polymarket evidence. Test fixed Kalshi candidates on Polymarket and, separately, allow discovery of Polymarket-specific cells using only its development period. Neither venue should be assumed to be the easier market.

## What should be tested next

1. Preserve this entire inventory and the failed candidates. Before a new evaluation, choose a manageable candidate family using development evidence and a stated rule; do not choose the largest validation winner from this catalog.
2. Establish an admissible, previously unexamined evaluation interval and sufficient earlier training history. Keep the existing protected protocol unchanged. Inventory Polymarket coverage in parallel with Kalshi quote-quality improvements.
3. Compare raw market, market-only calibration, reference 24 and the applicable frozen candidate models on the same rows. Report prediction scores first, then a separate trading evaluation.
4. For trading, the existing pipeline already includes fee logic. Reuse and verify it for the relevant contracts; add actual available entry prices, spread, latency, size and fill assumptions. A midpoint probability gap is not net profit. Limit-order variants require realistic fill/adverse-selection checks.
5. Report results by venue and pocket, then their combined policy. Account for multiple tests and overlapping contracts; do not count correlated model variants or5m/8m observations as independent successes.

This packet authorizes no trading and claims no new profitable strategy. It documents all saved leads so the next bounded test can consider them without forgetting weaker or failed evidence.

## Reproducibility and handoff

The retained operation joins the two completed cell maps and their inference tables, verifies counts and identical market cohorts across methods, attaches the original selection locks, and applies the descriptive rules above. Completed original outputs were not modified or rerun.

- [All 100 cells, remotely viewable CSV](2026-10-18-candidate-inventory-cells.csv).
- [Evidence, procedure, fingerprints and complete cell records](2026-10-18-candidate-inventory-evidence.json).
- [Original aligned 57 results](2026-10-18-warmup20-aligned-methods.md), [tuning 20 results](2026-10-18-pocket-tuning.md), [attribution diagnostic](2026-10-18-attribution.md).
- Local full 7,700-pair export and aggregation log: /home/russell/data/crypto_trading/research/candidate-inventory-20261018.

Verification and independent review outcomes are recorded in the evidence file. No full implementation test suite is required for this documentation synthesis. Original training, timing and repeated-validation limitations continue to apply.
