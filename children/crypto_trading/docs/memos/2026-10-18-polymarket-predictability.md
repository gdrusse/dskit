# Polymarket prediction study — 2026-10-18

## TL;DR

**There are tentative prediction leads, especially BTC at 5 minutes with UP priced 80–90%, but no pocket passed the full multiple-comparison check.** We reused existing Polymarket history and completed 20 methods, 40 training/scoring JSONs and 240 output files. This is a BTC calibration replication against historical reported prices; ETH and the Kalshi GRU were not transferred.

## Execution contract and data

The owner requested the same prediction-versus-market analysis using existing Polymarket data. We used saved official CLOB price-history decisions and saved Polymarket UP settlements, not Kalshi labels or probabilities. Available history covers September 2025–July 2026; this experiment restricts to the previous Kalshi study's calendar. The original summary names official /batch-prices-history, one-minute fidelity and a 90-second maximum age.

Registered, acquired, hash-verified snapshots now pin the existing export and derived panel. That establishes reproducibility from those exports onward; complete original downloader/raw-response hashes were not located. Six deterministic boundary-date checks against newly acquired official Gamma metadata agreed on outcome, scheduled close and original string token. Reported closure was 22–86 seconds after scheduled end in those six cases. That small check does not validate all contracts.

**Market baseline:** the historical scalar price p, named mid in reused scoring vocabulary. It is not independently established as contemporaneous bid/ask midpoint. [Official batch-history documentation](https://docs.polymarket.com/api-reference/markets/get-batch-prices-history) describes historical prices and fidelity but does not resolve that construction here. Kalshi used closed-minute bid/ask midpoint. Same dates and scores therefore do not make the two venue baselines identical.

Fees and execution are outside this prediction study. A settlement-minus-price gap is not a realizable profit estimate. No trade, production change, protected Kalshi forward read or automation was launched.

## Exact windows and training losses

All timestamps are UTC. Candidate models and the independent lead-logistic benchmark share eligible rows and cutoffs.

- **February 19 fit:** 31,018 training forecasts from 6,213 contracts, scheduled settlements December 15–February 17 at 23:45. Score February 19–March 20: 14,353 forecasts.
- **March 21 fit:** 45,376 training forecasts from 9,092 contracts, settlements December 15–March 19 at 23:45. Score March 21–April 19: 14,371 forecasts.
- **May 2 frozen fit:** 60,226 training forecasts from 12,068 contracts, settlements December 15–April 19 at 23:45. Score **May 2–July 31:** 43,482 forecasts from 8,714 contracts over 91 days. No May–July outcome enters fitting.
- April 20–May 1 is excluded to match the earlier Kalshi panel gap. Warm-up totals 28,724 forecasts from 5,758 contracts over 60 days. Leads are 2, 5, 8, 11 and 14 minutes.
- Eligibility requires scheduled close plus 24 hours strictly before the fit cutoff. Full-history actual publication times are missing, so this is an explicit timing assumption, not proven point-in-time label availability.

[Training and scored losses](2026-10-18-polymarket-predictability-training.csv) gives every method/fit, boundaries, counts and market/model/calibrated-market losses. Those comparisons weight forecast rows equally within each block. [Overall method comparisons](2026-10-18-polymarket-predictability-methods.csv) first average each contract's available leads, then weight contracts equally. Cell comparisons have one UP forecast per contract; DOWN is complementary, not another independent observation.

These dates were inspected in previous Polymarket research. Freezing this run's choices before its new validation replay does **not** make historical validation untouched.

## Models and training

Methods 01–05 are pooled sigmoid, pooled beta, pooled isotonic, lead-specific beta and lead-specific isotonic calibration. Methods 06–10 fit ridge, ARD, Bayesian ridge, elastic-net and Huber corrections to market residuals. Methods 11–16 use market-curvature corrections with ARD, ridge, Bayesian ridge, fixed splines, fixed radial kernels and source-age interactions. Methods 17–20 test residual histogram boosting, direct log-loss histogram boosting, random forest and a small tanh network.

Features are Polymarket probability, lead or source age and fixed transformations. No spot, volume, order-book depth, Kalshi forecast or GRU is substituted. This tests whether native probabilities can be recalibrated; it is **not** the full earlier 57-method feature/model experiment.

Each [run JSON](../../configs/run-polymarket20-validation-02-pooled-beta.json) names the source and estimator/feature/target/anchoring parameters; all 40 are alongside it. Existing DSKit nodes train and score. Lower log loss and Brier scores are better. Residual predictions are restrained toward market estimates; validation does not change that restraint.

**Main lead's model 02:** pooled beta calibration fits sigmoid(a × log(p) + b × log(1−p) + c), using logistic regression with C=0.05, at most 500 iterations, and probabilities clipped to [0.000001,0.999999]. Coefficients are shared across leads, not fitted only to the attractive pocket. Labels are native UP settlements. The independent comparator fits a sigmoid to market log-odds separately for each lead.

Across validation contracts, model 02 log loss is 0.502826, raw market 0.503055, lead calibration 0.502845. Its Brier score is slightly worse than lead calibration. It also did not beat raw market overall in warm-up. The potential lead is local, not global.

## Exhaustive cells and selection

[All 50 potential cells](2026-10-18-polymarket-predictability-inventory.csv) include empty and weak regions. Warm-up has 46 populated cells; validation has 47. Complete comparisons are in [warm-up cells](2026-10-18-polymarket-predictability-warmup-cells.csv), [validation cells](2026-10-18-polymarket-predictability-validation-cells.csv), [warm-up leads](2026-10-18-polymarket-predictability-warmup-leads.csv) and [validation leads](2026-10-18-polymarket-predictability-validation-leads.csv). They contain realized frequency, model/raw/calibrated probabilities, both losses and uncertainty.

Before validation execution, retain every warm-up method/cell with at least 200 contracts and 20 days and lower log loss **and** Brier than both raw and lead-logistic prices. No top-k cutoff:

- 152 method/cell pairs in 32 cells qualified.
- 29 pairs in 13 cells retained required mean-score improvements in validation.
- 25 cells have some two-period raw-price support, including calibration-only leads.
- **Zero adequately sampled cells passed both day- and week-clustered significance after search correction.**

The 13 cells with a previously selected surviving model are: lead 2 at 20–30, 30–40, 40–50, 50–60, 80–90 and 90–100%; lead 5 at 0–10, 20–30 and 80–90%; lead 8 at 30–40, 50–60 and 90–100%; and lead 11 at 60–70%. No lead-14 cell retained the required two-period support. These share outcomes and are not 13 independent opportunities.

As a descriptive diagnostic, the method ranked best by warm-up calibrated-market log loss in each selected cell survived in 7 of 32 cells. That best-per-cell diagnostic was not a separately frozen strategy. All original selected sets and failures remain visible.

## Candidate priorities

**First: BTC, lead 5, UP 80–90%, model 02.** Warm-up: 617 contracts, market 85.16%, realized 86.87%; log-loss improvement 0.000909 versus raw and 0.000179 versus calibration. Validation: 884 contracts, market 85.24%, model 86.25%, calibration 86.02%, realized **88.57% (783 UP settlements)**.

Validation log loss: raw 0.355820, model 0.353404, calibration 0.353896. Brier: raw 0.101413, model 0.100844, calibration 0.100958. Model-minus-raw log loss is −0.002416; model-minus-calibration is −0.000493. The hindsight 3.33-point settlement gap is larger than the fitted 1.01-point correction; do not substitute the realized 88.57% for the prediction.

Post-validation diagnostics show improvement versus raw in both May 2–June 15 (436 contracts, −0.003529 log loss) and June 16–July 31 (448 contracts, −0.001334). This supports a retest, not independent confirmation.

**Secondary: BTC, lead 2, UP 80–90%, model 02.** 605 validation contracts; market 85.33%, realized 86.78%; log-loss improvement 0.000753 versus raw and 0.000448 versus calibration. Raw-market log loss improves in both halves, but uncertainty is wide.

**Lower-priority reserves:** lead 5 UP 0–10% with pooled isotonic method 03 (1,509 contracts; market 4.81%, realized 4.44%; log-loss improvement 0.000335 versus raw and 0.000246 versus calibration). Also lead 2 UP 90–100% with model 02 (2,485 contracts) and lead 8 UP 90–100% with Huber method 10 (615 contracts). Both full-period improvements reverse in the first validation half. Lead 11 UP 60–70% with method 10 has a tiny improvement and fails the second-half raw comparison. Other surviving cells remain documented but have weaker or unstable calibration-gap evidence.

## Why this remains tentative

The declared family is 20 methods × 50 potential cells = 1,000 comparisons, conservatively including empty cells. One-sided paired log-loss tests cluster on settlement day and separately on fixed seven-day blocks. Adjusted p = min(1,1000 × p), threshold 0.05 per comparator; require both cluster definitions and Brier improvement. Bonferroni does not assume independent cells; clustered t approximations and limited weeks remain assumptions. This does not account for every earlier project experiment.

For the primary lead, unadjusted day p-values are 0.001493 versus raw and 0.005092 versus calibration; week values are 0.008488 and 0.016378. All four adjusted values are 1.0. It is interesting before allowing for the search size, but fails the declared search-wide bar.

Median source age is 49 seconds, 95th percentile 57. In the primary validation cell, 882 of 884 prices were 31–60 seconds old; only 2 were at most 30 seconds old. This cannot establish an advantage against fresh prices. No aligned ETH panel was established. Existing August–October BTC WebSocket books were located but not mixed into the same-calendar study.

## Checks, limitations and next step

All 40 documents planned and completed, with 240 hash-attested outputs. Independent numerical reconstruction reproduced every candidate/comparator prediction within 1e-10 across all 60 method/fit blocks and retained training/scored losses. The 72 focused expanding-refit tests passed; no full suite ran. Model 20 reached its fixed 100-iteration cap, so it is not an optimized neural-model result.

Three smoke failures were repaired: inherited five-point-bin collision, missing output directory and an incorrect assumption that all 50 cells were populated. Failed runs remain recorded and are excluded from evidence.

One non-model metadata limitation remains: pandas converted unused prepared token_id values to numbers with lost precision. **Do not use that column for token joins.** Slugs remain exact; original string tokens remain in the acquired source and six sampled originals match Gamma. No feature, grouping, label join or score uses the numeric column.

**Continue with a narrow replication, not another broad tuning search.** Freeze model 02 and lead 5 UP 80–90% as the primary follow-up, lead 2 UP 80–90% secondary. Test against explicitly computed, timestamp-aligned midpoints and native settlements using the existing WebSocket books. That later period was also researched, so call it retrospective transfer; an untouched period is still needed for confirmation. Align oracle targets before adding spot/GRU models or ETH. If the gain disappears against fresh midpoint or remains unstable across periods, stop this calibration-only approach.

The [evidence packet](2026-10-18-polymarket-predictability-evidence.json) retains configuration/source hashes, selection lock, run-output manifests, training audit and executable research scripts. Scripts compose existing nodes; no toolkit mechanism was added. Use the WSL project environment. Data remains local; JSONs, memo, training comparisons and full cell summaries are versioned for remote viewing. Independent review and publication status are appended to the evidence before closure.
