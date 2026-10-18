# Focused tuning and stable probability pockets

## Best candidate options

No preselected pocket passes the declared raw-market robustness screen. Tuning has not established a stable local prediction advantage.

Retain method 24 as the reference. Treat settings below as historical research candidates, not established replacements. Parent choices and both periods were already inspected; this is a robustness check on reused history.

**Most useful local follow-up:** preserve the already selected ETH / 11m / 10-20% market-probability region as a fixed research lead. All four parent families selected it on warm-up. On validation it contains 689 contracts, with mean market YES 15.41% and realized YES 14.22%; all four tuned models improve mean log loss versus market and their own parent in both validation halves. None passes the corrected uncertainty tests. This is consistent direction, not a validated pocket or a reason to select a new winning setting from validation.

Completed twenty settings (sixteen perturbations and four exact controls), each on warm-up and validation: forty pipeline JSONs, 240 output files and 4,000 asset/lead/bin cell rows. The warm-up lock selected 4 global settings and 8 pockets before validation execution.

### Frozen global choices, evaluated on validation

| Setting | Parent | Log loss vs market | Daily / weekly adjusted p | vs recalibration | vs method 24 | vs exact parent |
|---|---:|---:|---:|---:|---:|---:|
| 03 p24-strength-075 | 24 | -0.0007584 | 0.05478 / 0.1918 | -0.0008877 | +0.0000168 | +0.0000168 |
| 09 p44-strength-075 | 44 | -0.0007091 | 0.1054 / 0.277 | -0.0008383 | +0.0000662 | +0.0000666 |
| 12 p38-strength-075 | 38 | -0.0007417 | 0.05627 / 0.1974 | -0.0008709 | +0.0000335 | +0.0000167 |
| 19 p48-vol-boundary-050 | 48 | -0.0007073 | 0.005682 / 0.0561 | -0.0008365 | +0.0000680 | +0.0000834 |

Negative differences favor the tuned candidate. These choices were selected on warm-up stability, not validation ranking. Exact parent controls reproduce their earlier aligned predictions bit-for-bit in both periods.

### Frozen pocket results

| Setting / parent | Asset / lead / market bin | Contracts | Market YES | Actual YES | Model YES | Log loss vs market | vs recalibration | Daily / weekly p vs market | Robust raw pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 03 / 24 | ETH / 11m / 10-20 | 689 | 15.41% | 14.22% | 14.68% | -0.003899 | -0.003232 | 0.1943 / 0.1397 | False |
| 03 / 24 | ETH / 11m / 20-30 | 1022 | 25.08% | 24.56% | 24.39% | -0.000402 | -0.000642 | 1 / 1 | False |
| 06 / 48 | ETH / 11m / 10-20 | 689 | 15.41% | 14.22% | 14.62% | -0.004005 | -0.003338 | 0.2187 / 0.07857 | False |
| 06 / 48 | ETH / 14m / 70-80 | 397 | 73.76% | 73.80% | 74.26% | -0.003023 | -0.002998 | 1 / 1 | False |
| 09 / 44 | ETH / 11m / 10-20 | 689 | 15.41% | 14.22% | 14.23% | -0.004211 | -0.003544 | 0.284 / 0.07858 | False |
| 09 / 44 | ETH / 14m / 70-80 | 397 | 73.76% | 73.80% | 74.73% | -0.002234 | -0.002209 | 1 / 1 | False |
| 12 / 38 | ETH / 11m / 10-20 | 689 | 15.41% | 14.22% | 14.86% | -0.003701 | -0.003035 | 0.1839 / 0.1677 | False |
| 12 / 38 | ETH / 11m / 20-30 | 1022 | 25.08% | 24.56% | 24.60% | -0.000456 | -0.000696 | 1 / 1 | False |

Each robust pass requires both daily and weekly corrected uncertainty, non-worsening Brier score and the same gain direction in both validation halves, separately per comparator. Raw-market improvement is distinct from improvement beyond recalibration or the exact parent.

All 8 frozen model/pocket pairs have lower mean validation log loss than raw and recalibrated market; 7 retain the raw-market improvement in both validation halves. The uncertainty screens still pass none. This distinguishes a tentative repeated direction from a demonstrated advantage.

### Screen totals

- raw: global daily passes [19]; local daily passes []; robust local passes [].
- calibrated: global daily passes [3, 9, 12, 19]; local daily passes []; robust local passes [].
- prior05: global daily passes []; local daily passes []; robust local passes [].
- prior24: global daily passes []; local daily passes []; robust local passes [].
- parent: global daily passes []; local daily passes []; robust local passes [].

## All twenty settings

| Setting | Parent | Warm-up log loss vs market | Validation vs market | Validation vs recalibration | Validation vs exact parent |
|---|---:|---:|---:|---:|---:|
| [01 p24-strength-025](../../configs/run-pocket-tuning-validation-01-p24-strength-025.json) | 24 | -0.0008301 | -0.0005266 | -0.0006559 | +0.0002486 |
| [02 p24-strength-050](../../configs/run-pocket-tuning-validation-02-p24-strength-050.json) | 24 | -0.0013401 | -0.0007752 | -0.0009045 | +0.0000000 |
| [03 p24-strength-075](../../configs/run-pocket-tuning-validation-03-p24-strength-075.json) | 24 | -0.0015457 | -0.0007584 | -0.0008877 | +0.0000168 |
| [04 p48-strength-025](../../configs/run-pocket-tuning-validation-04-p48-strength-025.json) | 48 | -0.0007985 | -0.0005368 | -0.0006661 | +0.0002539 |
| [05 p48-strength-050](../../configs/run-pocket-tuning-validation-05-p48-strength-050.json) | 48 | -0.0012522 | -0.0007907 | -0.0009199 | +0.0000000 |
| [06 p48-strength-075](../../configs/run-pocket-tuning-validation-06-p48-strength-075.json) | 48 | -0.0013763 | -0.0007749 | -0.0009041 | +0.0000158 |
| [07 p44-strength-025](../../configs/run-pocket-tuning-validation-07-p44-strength-025.json) | 44 | -0.0008467 | -0.0005446 | -0.0006738 | +0.0002311 |
| [08 p44-strength-050](../../configs/run-pocket-tuning-validation-08-p44-strength-050.json) | 44 | -0.0013641 | -0.0007757 | -0.0009049 | +0.0000000 |
| [09 p44-strength-075](../../configs/run-pocket-tuning-validation-09-p44-strength-075.json) | 44 | -0.0015671 | -0.0007091 | -0.0008383 | +0.0000666 |
| [10 p38-strength-025](../../configs/run-pocket-tuning-validation-10-p38-strength-025.json) | 38 | -0.0008046 | -0.0005153 | -0.0006445 | +0.0002431 |
| [11 p38-strength-050](../../configs/run-pocket-tuning-validation-11-p38-strength-050.json) | 38 | -0.0012937 | -0.0007584 | -0.0008876 | +0.0000000 |
| [12 p38-strength-075](../../configs/run-pocket-tuning-validation-12-p38-strength-075.json) | 38 | -0.0014826 | -0.0007417 | -0.0008709 | +0.0000167 |
| [13 p24-pruning-1000](../../configs/run-pocket-tuning-validation-13-p24-pruning-1000.json) | 24 | -0.0013469 | -0.0007819 | -0.0009111 | -0.0000067 |
| [14 p24-pruning-100000](../../configs/run-pocket-tuning-validation-14-p24-pruning-100000.json) | 24 | -0.0013363 | -0.0007751 | -0.0009043 | +0.0000001 |
| [15 p48-pruning-1000](../../configs/run-pocket-tuning-validation-15-p48-pruning-1000.json) | 48 | -0.0012595 | -0.0007903 | -0.0009196 | +0.0000003 |
| [16 p48-pruning-100000](../../configs/run-pocket-tuning-validation-16-p48-pruning-100000.json) | 48 | -0.0012489 | -0.0007905 | -0.0009197 | +0.0000002 |
| [17 p44-pruning-1000](../../configs/run-pocket-tuning-validation-17-p44-pruning-1000.json) | 44 | -0.0013691 | -0.0007831 | -0.0009124 | -0.0000074 |
| [18 p44-pruning-100000](../../configs/run-pocket-tuning-validation-18-p44-pruning-100000.json) | 44 | -0.0013615 | -0.0007755 | -0.0009048 | +0.0000002 |
| [19 p48-vol-boundary-050](../../configs/run-pocket-tuning-validation-19-p48-vol-boundary-050.json) | 48 | -0.0012170 | -0.0007073 | -0.0008365 | +0.0000834 |
| [20 p48-vol-boundary-200](../../configs/run-pocket-tuning-validation-20-p48-vol-boundary-200.json) | 48 | -0.0012904 | -0.0007558 | -0.0008851 | +0.0000349 |

The full twenty-setting / 2,000-cell scans are exploratory, separately corrected per comparator. They do not replace the frozen global or pocket choices.

## Next decision

Stop further strength/pruning/regime-boundary tuning on this same history. Retain method 24 as the reference and preserve the locked settings as a completed negative or inconclusive local test. Preserve ETH / 11m / 10-20% as a fixed follow-up region, with its settings unchanged. The useful next work is an information-quality investigation: more faithful decision-time market probabilities and independently timed features, followed by a separately fixed historical comparison or unexamined outcomes. Another parameter sweep on these dates would add selection risk without resolving the failed pocket replication.

## Frozen design and interpretation

## Execution contract fixed before runs

Owner request: further iterate on the best methods, tune relevant settings and check whether predictions beat market in specific pockets. Base: d92ab16c4308597565e47780c7b2a47e58f45af8. Allowed changes: child JSON configurations, focused tests, this memo/evidence, generated research journal and final lane handoff. No toolkit mechanism, upstream panel rebuild, inherited GRU retraining, trading, deployment or protected forward outcome read.

Twenty settings comprise four exact parent controls and sixteen perturbations. For each parent 24/48/44/38, test market-logit correction strengths .25/.50/.75. For each ARD parent 24/48/44, also test coefficient-precision pruning thresholds 1,000/100,000 at strength .50 (default 10,000). For parent 48 also test fixed volatility boundaries at half/twice its previous level, at strength .50. This is a bounded one-factor-at-a-time sensitivity study, not an exhaustive interaction grid.

Every setting has warm-up and validation JSONs with the same six full output views as the aligned study: forecasts, contract scores, asset/lead/market-decile cells, leads, asset/leads and lead/decile calibration. All candidate and benchmark training schedules, data rows, features and comparator definitions remain identical to that study. Warm-up scoring: February 19-April 19, 2026, 52,999 forecasts / 11,213 contracts; causal fits February 19 and March 21. Validation: May 2-July 31, 84,728 forecasts / 17,158 contracts; all corrections and fitted comparators train once at May 2, using settlement plus one day strictly before cutoff. The inherited GRU retains its original causal update schedule. Data are Kalshi BTC/ETH; market probability is the acquired one-minute candle-midpoint proxy.

All these dates have already been inspected. Parent choices use that earlier knowledge. This study can test historical robustness, but cannot supply an untouched test or erase previous selection. Keep the separate protected forward protocol unchanged.

## Warm-up-only tuning and pocket selection

Split the 60 warm-up days into four consecutive 15-day windows, and also two 30-day halves. Use retained causal predictions, never training predictions. A global setting qualifies only when mean log-loss differences versus both raw market and its calibrated benchmark are negative in both halves, and mean Brier difference versus raw market is nonpositive in both halves. Select at most one setting per parent family, ranked by its worst-half calibrated-market log-loss difference (smaller is better), then full-period calibrated difference, then setting ID. This prevents a large single-period gain from dominating the choice.

For local pockets, examine all 100 fixed asset/lead/market-decile cells for every setting. Require at least 200 observations and 20 distinct days overall, and at least 30 observations / 5 days in each of the four windows. Require negative log-loss differences versus raw AND calibrated market in both halves, negative differences versus both in at least three of four windows, and nonpositive full-period Brier differences versus both. Within each parent family, rank qualifying setting/cell pairs by worst-half calibrated log-loss difference, then full-period calibrated difference, setting ID, asset, lead and bin. Select at most two distinct cell identities per parent, keeping the best qualifying setting for each cell. Global and pocket settings can differ because their declared objectives differ. Do not fill vacancies or relax rules if none qualify.

Freeze all setting IDs, cell identities, source/configuration/script hashes, warm-up output hashes and these rules in a committed selection lock before validation runs. All twenty settings still receive complete validation outputs; unselected results remain exploratory. There is no tuning after validation.

## Validation scoring and decision rule

Compare each frozen global setting and each frozen cell against raw market, logistic market recalibration, prior method 05, prior method 24 and its own exact parent control. Negative paired log-loss/Brier differences favor the candidate. Use contract-averaged scores globally and one contract observation at each cell lead. Report day-cluster and fixed-seven-day-cluster one-sided t uncertainty, with Bonferroni correction separately for each comparator and selection family: at most four global choices and eight pockets. Family size is the actual frozen number; an empty family has no passes. Cross-comparator families are separate, not a joint five-comparator claim.

A daily screen requires negative log loss, corrected p below .05 and nonpositive Brier difference versus that same comparator. A robust pocket must additionally pass the seven-day screen and have negative log-loss differences in both validation halves (May 2-June 15 and June 16-July 31). Report actual sample sizes and realized YES, mean market and model probabilities. Distinguish improvement over raw market from improvement beyond simple recalibration, and tuning gains from changes versus the exact parent.

All twenty-setting / 2,000-cell maps are exploratory, with the stated family correction per comparator. No unselected success replaces a frozen choice. The main decision is whether stable warm-up pockets repeat and whether tuning adds anything beyond the exact parent controls. If none does, retain the simpler reference and stop tuning these settings on this history.

## Implementation and verification

Reuse the existing generic expanding refitter, compute/group/score and immutable-output nodes. Focused tests must verify all 40 documents, identical source and comparator definitions, causal feature/fit boundaries and exact parent controls. The author audit checks every output identity, score, aggregate and fit census. Two fresh independent final reviews are required by the repository workflow. Full suite is not requested.

Initial RED: the focused tests correctly failed because the forty requested documents and four control pairs were absent. No models had run. Sweep found no competing pocket-tuning implementation; the larger synonym sweep hit its 0.05-second text-search budget, and the focused sweep completed across 76 refs / 74 other worktrees.

## References and reproducibility

- [Chronological evaluation and model selection](https://scikit-learn.org/stable/modules/cross_validation.html)
- [ARD coefficient pruning parameter](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.ARDRegression.html)
- [Previous aligned study](2026-10-18-warmup20-aligned-methods.md)
- Artifacts: /home/russell/data/crypto_trading/research/pocket-tuning-20261018.

### Operational retry before selection

Warm-up setting 01 initially stopped at the first writer because the requested results directory was absent. Its model fit completed, but no completed six-output result existed. The failed run and original log are retained; only output placement for its retry changed. The folder was explicitly created, and the retry uses a new run root without overwriting the failure. This is a recorded execution failure, not a completed experiment or a statistical-plan change.


## Completed verification and reproducibility

All forty completed runs have six outputs. The full author audit verified 240 output hashes/counts, 501 causal training/prediction censuses, exact shared cohorts and benchmark predictions, all five proper-score series, and every aggregate field. All four controls reproduce the previous study exactly. The focused suite passed 127 tests; six synthetic stability probes and Ruff passed. The full suite was not run. Independent reviews are pending.

One initial warm-up attempt failed at output writing because the results directory was absent. It is retained separately and excluded from the forty completed experiments. Its retry changed output placement only. No failed or partial experiment counts as success.

The original candle/spot source generation and GRU training were inherited, not rebuilt. Day/week clustering is a dependence sensitivity check; neither proves independence. Families are corrected separately per comparator. Historical reuse remains the largest statistical limitation.

Artifacts: /home/russell/data/crypto_trading/research/pocket-tuning-20261018. Reproduction requires the acquired local source, retained scripts and recorded package versions. Outputs refuse overwrite; use unused output placement for a rerun.

- [Evidence packet](2026-10-18-pocket-tuning-evidence.json)
- [All settings](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/all-settings.csv)
- [Selected pocket comparisons](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/selected-pockets.csv)
- [All cells with uncertainty](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/all-cell-inference.csv)
- [Cell stability by half](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/cell-half-stability.csv)
- [Per-lead outputs](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/all-leads.csv)
- [Calibration outputs](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/all-calibration.csv)
- [Frozen warm-up selection](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/selection-lock.json)
