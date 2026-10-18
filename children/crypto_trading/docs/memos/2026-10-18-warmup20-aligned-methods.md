# Twenty new approaches with aligned historical development and validation

## Best candidate options

**Decision: continue narrowly with method24 as the reference and method48 as the new follow-up option.** Method48 reduces validation log loss by 0.174% versus raw market; its seven-day-block adjusted p=.0492 is borderline, and it does not establish an improvement over method24. Methods44 and38 are close alternatives. The frozen warm-up ordering below is unchanged.

**Local candidates:** the earlier ETH 5m/8m upper-tail regions retain the same calibration-gap direction, but no preselected cell validates. Method02 isotonic calibration in ETH 5m / 90-100% has a consistent score-improvement direction across the split and is an exploratory follow-up lead only; its weekly-adjusted test fails. Do not promote the method57 local hit: it was worse than market on that cell during warm-up.

**Twenty new methods and all 37 earlier experiments have completed the same two-stage comparison.** The 114 JSON runs produced 684 output files, including 11,400 asset/lead/bin cells and full lead/calibration summaries. Recommendations below use the development-only shortlist, then its separate validation results. These dates were previously researched, so this is historical replication, not untouched confirmation.

### New candidates, in frozen warm-up order

- **44 interaction-sparse-newton**: development log-loss difference versus market -0.001364; validation -0.000776. Validation versus calibrated market -0.000905 (adjusted p=0.0008855); versus prior method24 -0.000000 (adjusted p=1). It passes raw-market and calibrated-market screens.
- **38 bayesian-newton**: development log-loss difference versus market -0.001294; validation -0.000758. Validation versus calibrated market -0.000888 (adjusted p=0.0008548); versus prior method24 +0.000017 (adjusted p=1). It passes raw-market and calibrated-market screens.
- **48 volatility-expert-newton**: development log-loss difference versus market -0.001252; validation -0.000791. Validation versus calibrated market -0.000920 (adjusted p=0.0004009); versus prior method24 -0.000015 (adjusted p=1). It passes raw-market and calibrated-market screens.

### Earlier candidates, re-evaluated on the same split

- **24 newton-logloss-sparse**: development log-loss difference versus market -0.001340; validation -0.000775. Validation versus calibrated market -0.000904 (adjusted p=0.0007649); versus prior method24 +0.000000 (adjusted p=1). It passes raw-market and calibrated-market screens.
- **05 ard-sparse-residual**: development log-loss difference versus market -0.001167; validation -0.000622. Validation versus calibrated market -0.000752 (adjusted p=0.003906); versus prior method24 +0.000153 (adjusted p=1). It passes raw-market and calibrated-market screens.
- **22 asset-lead-sparse-residual**: development log-loss difference versus market -0.001099; validation -0.000602. Validation versus calibrated market -0.000731 (adjusted p=0.003336); versus prior method24 +0.000174 (adjusted p=1). It passes raw-market and calibrated-market screens.

Negative score differences favor the candidate. The shortlist was frozen before any new validation execution; validation has not been used to replace a shortlisted method with an unselected winner. All cells and methods are reported below or in the linked artifacts.

## Execution contract

The user requested 20 additional approaches and an apples-to-apples reassessment of all 37 previous experiments. Base commit: 93e85bd575c509e1fb3277a7cb5c32ee7615e98e. Scope: 57 methods, each with a development JSON and a frozen validation JSON, six complete output views per stage. No trading, deployment, new toolkit mechanism or protected forward-label read.

Development uses the earlier acquired history, with causal training and scoring February 19-April 19, 2026. The reader excludes May 2 onward. Models refit every 30 days using only settlement published plus one day strictly before each cutoff. Validation scores May 2-July 31 with one fit at May 2 and no later fitting. Candidate and all three fitted benchmarks share the same eligible cohort, cutoff and one-day embargo. Methods 33 and 34 therefore become duplicate schedule-normalized method05 controls; their earlier schedule distinctions are not tested in this aligned replay.

Development contains 52,999 forecasts / 11,213 contracts / 60 days. Validation contains 84,728 forecasts / 17,158 contracts / 91 days. Ten forecasts belonging to two contracts with decisions before the validation boundary are excluded by the frozen block. All comparisons include raw midpoint, per-lead logistic market recalibration, method05 ARD and method24 Newton ARD, freshly fitted under the matching schedule. The inherited GRU input has its original chronological training schedule; this study freezes residual/calibration models, not the inherited GRU backbone. Same GRU inputs for every candidate.

Both periods were previously inspected. The strict new workflow prevents additional validation-driven selection within this batch; it cannot erase prior knowledge or establish untouched confirmation. Earlier combined-period recommendations are superseded by this aligned assessment.

## Selection rule fixed before runs

After all 57 development results, shortlist at most three previous methods (excluding duplicate schedule-normalized 33/34) and three new methods. Eligibility requires negative mean contract-level log loss and Brier differences versus raw market. Rank eligible candidates by mean contract-level log-loss difference versus calibrated market, with method ID as tie-breaker. No validation result participates.

For each shortlisted method, select at most three fixed asset/lead/market-decile cells with at least 200 forecasts and 20 days, negative log loss versus calibrated market, and negative Brier versus raw market; rank by calibrated-market log-loss difference. Freeze these identities, configurations, development-result hashes and analysis rules before launching validation. All 57 validation methods still receive complete reports; unshortlisted winners are descriptive only.

Primary validation uses the frozen shortlist size as the global family and the frozen selected-cell count as the cell family, separately for each comparator. A candidate or cell passes a comparator only with negative mean log loss, one-sided UTC-day cluster t p-value adjusted below .05, and nonpositive Brier difference versus that same comparator. Fixed seven-day cluster sensitivity is also reported. The full 57-global / 5700-cell maps are exploratory. These corrections do not undo earlier adaptive historical reuse. No ranking changes or retuning after validation.


## What validation retained

All six shortlisted methods pass the day-clustered raw-market and recalibrated-market screens. None passes against either prior sparse benchmark. Weekly raw-market adjusted p-values are method24=.0515, 05=.0812, 22=.1113, 44=.0686, 38=.0539 and 48=.0492. The apparent global gain is therefore sensitive to the dependence assumption. All six retain negative raw-market score differences in both validation blocks; incremental differences among the new candidates and method24 change sign across the blocks.

- Frozen shortlist versus raw market: passing methods [24, 5, 22, 44, 38, 48]; passing preselected cells: none.
- Frozen shortlist versus market recalibration: passing methods [24, 5, 22, 44, 38, 48]; passing preselected cells: none.
- Frozen shortlist versus prior method05: passing methods none; passing preselected cells: none.
- Frozen shortlist versus prior method24: passing methods none; passing preselected cells: none.

### Preselected cells

All cells below were chosen solely from warm-up. Counts and scores are validation-only. The adjusted p-value uses the complete frozen cell family, separately for each comparator.

| Method | Asset / lead / market bin | Contracts | Market YES | Realized YES | Model YES | Log loss vs market | vs recalibration | Adjusted p vs recalibration |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 24 | ETH / 5m / 20-30% | 598 | 24.56% | 24.25% | 23.86% | -0.000939 | -0.002151 | 1 |
| 24 | BTC / 11m / 10-20% | 721 | 15.56% | 14.42% | 14.97% | +0.000224 | +0.001067 | 1 |
| 24 | ETH / 2m / 20-30% | 275 | 24.61% | 29.45% | 23.71% | +0.000676 | -0.013805 | 0.1269 |
| 05 | BTC / 5m / 30-40% | 480 | 34.92% | 34.17% | 35.11% | -0.000782 | -0.000791 | 1 |
| 05 | BTC / 2m / 10-20% | 385 | 14.60% | 12.99% | 14.66% | +0.000094 | -0.000991 | 1 |
| 05 | ETH / 5m / 20-30% | 598 | 24.56% | 24.25% | 24.63% | -0.000458 | -0.001669 | 1 |
| 22 | ETH / 5m / 20-30% | 598 | 24.56% | 24.25% | 24.35% | -0.000393 | -0.001605 | 1 |
| 22 | ETH / 11m / 10-20% | 689 | 15.41% | 14.22% | 14.53% | -0.003491 | -0.002824 | 0.6848 |
| 22 | BTC / 11m / 10-20% | 721 | 15.56% | 14.42% | 15.20% | +0.000096 | +0.000940 | 1 |
| 44 | BTC / 5m / 30-40% | 480 | 34.92% | 34.17% | 34.64% | -0.001651 | -0.001660 | 1 |
| 44 | BTC / 2m / 10-20% | 385 | 14.60% | 12.99% | 13.78% | -0.000547 | -0.001632 | 1 |
| 44 | ETH / 5m / 20-30% | 598 | 24.56% | 24.25% | 23.56% | -0.000784 | -0.001995 | 1 |
| 38 | ETH / 5m / 20-30% | 598 | 24.56% | 24.25% | 23.86% | -0.000973 | -0.002184 | 1 |
| 38 | BTC / 11m / 10-20% | 721 | 15.56% | 14.42% | 15.15% | +0.000222 | +0.001066 | 1 |
| 38 | ETH / 2m / 20-30% | 275 | 24.61% | 29.45% | 23.71% | +0.000537 | -0.013944 | 0.1305 |
| 48 | BTC / 5m / 30-40% | 480 | 34.92% | 34.17% | 34.43% | -0.001492 | -0.001501 | 1 |
| 48 | BTC / 2m / 10-20% | 385 | 14.60% | 12.99% | 13.76% | +0.000320 | -0.000765 | 1 |
| 48 | ETH / 5m / 20-30% | 598 | 24.56% | 24.25% | 23.88% | -0.001210 | -0.002421 | 1 |

### Lead-level comparisons for frozen new candidates

| Method | Lead | Forecasts | Log loss vs market | vs recalibration | vs prior method24 |
|---|---:|---:|---:|---:|---:|
| 44 | 2m | 16131 | -0.000602 | -0.002093 | -0.000037 |
| 44 | 5m | 17145 | -0.001037 | -0.000881 | -0.000129 |
| 44 | 8m | 17149 | -0.000989 | -0.000750 | +0.000075 |
| 44 | 11m | 17150 | -0.000495 | -0.000553 | +0.000041 |
| 44 | 14m | 17153 | -0.000462 | -0.000531 | +0.000044 |
| 38 | 2m | 16131 | -0.000596 | -0.002088 | -0.000031 |
| 38 | 5m | 17145 | -0.000919 | -0.000763 | -0.000012 |
| 38 | 8m | 17149 | -0.001044 | -0.000805 | +0.000019 |
| 38 | 11m | 17150 | -0.000500 | -0.000558 | +0.000036 |
| 38 | 14m | 17153 | -0.000436 | -0.000505 | +0.000070 |
| 48 | 2m | 16131 | -0.000567 | -0.002058 | -0.000001 |
| 48 | 5m | 17145 | -0.000894 | -0.000738 | +0.000013 |
| 48 | 8m | 17149 | -0.001100 | -0.000861 | -0.000037 |
| 48 | 11m | 17150 | -0.000571 | -0.000630 | -0.000036 |
| 48 | 14m | 17153 | -0.000509 | -0.000578 | -0.000003 |

## Earlier local recommendations, realigned

These are previously named regions, reported descriptively from the same stage-specific outputs; they were not added to the new frozen primary cell list. Method24 and the market-only calibrator are trained under the same cutoffs as every other method.

| Stage | ETH lead / market bin | Contracts | Market YES | Actual YES | Method24 log loss vs market | Calibration log loss vs market |
|---|---|---:|---:|---:|---:|---:|
| warmup | 5m / 90-100% | 1187 | 95.46% | 97.22% | -0.002638 | -0.003032 |
| warmup | 8m / 90-100% | 595 | 93.99% | 97.14% | -0.003083 | -0.005154 |
| warmup | 5m / 10-20% | 526 | 14.59% | 10.84% | -0.004057 | -0.005629 |
| validation | 5m / 90-100% | 1881 | 95.95% | 97.24% | -0.001599 | -0.002600 |
| validation | 8m / 90-100% | 921 | 94.11% | 96.09% | -0.002137 | -0.003406 |
| validation | 5m / 10-20% | 838 | 14.68% | 12.89% | -0.002950 | -0.000916 |

The upper-tail gaps retain their sign, but the market-calibrator score improvements do not pass a descriptive 100-cell adjusted day test in either period. A realized-frequency gap is not by itself a demonstrated forecast-score advantage.

The full validation scan has two raw-market cell hits under 5,700 daily tests: methods02 and57, both ETH 5m / 90-100%. Their adjusted p-values are .0401 and .00597, but both become 1.0 under weekly clustering; neither beats the calibrated benchmark. Method02 improves log loss in warm-up (-.001396) and validation (-.001513); method57 switches from worse (+.000383) to better (-.000643). These unshortlisted hits do not replace the frozen primary result: zero of 18 selected cells passes.

## All 57 aligned results

The first two score columns compare model log loss with raw market. The final column is the validation difference versus simple market recalibration. A shortlist mark means chosen on development, not a validation pass. Every method has both complete six-view reports.

| Method | Name | Warm-up vs market | Validation vs market | Validation vs calibration | Frozen shortlist |
|---|---|---:|---:|---:|---|
| 01 | [beta-calibration](../../configs/run-warmup20-validation-01-beta-calibration.json) | -0.000533 | +0.000087 | -0.000042 |  |
| 02 | [isotonic-calibration](../../configs/run-warmup20-validation-02-isotonic-calibration.json) | -0.000365 | -0.000159 | -0.000288 |  |
| 03 | [ridge-brier-residual](../../configs/run-warmup20-validation-03-ridge-brier-residual.json) | -0.000085 | +0.000809 | +0.000680 |  |
| 04 | [bayesian-ridge-residual](../../configs/run-warmup20-validation-04-bayesian-ridge-residual.json) | -0.001098 | -0.000565 | -0.000694 |  |
| 05 | [ard-sparse-residual](../../configs/run-warmup20-validation-05-ard-sparse-residual.json) | -0.001167 | -0.000622 | -0.000752 | yes |
| 06 | [elastic-net-residual](../../configs/run-warmup20-validation-06-elastic-net-residual.json) | -0.001049 | -0.000683 | -0.000812 |  |
| 07 | [huber-linear-residual](../../configs/run-warmup20-validation-07-huber-linear-residual.json) | -0.001044 | -0.000238 | -0.000367 |  |
| 08 | [shallow-tree-residual](../../configs/run-warmup20-validation-08-shallow-tree-residual.json) | -0.000661 | -0.000387 | -0.000517 |  |
| 09 | [random-forest-residual](../../configs/run-warmup20-validation-09-random-forest-residual.json) | -0.000612 | -0.000399 | -0.000529 |  |
| 10 | [extra-trees-residual](../../configs/run-warmup20-validation-10-extra-trees-residual.json) | -0.000452 | -0.000251 | -0.000380 |  |
| 11 | [histogram-brier-residual](../../configs/run-warmup20-validation-11-histogram-brier-residual.json) | -0.000514 | -0.000382 | -0.000511 |  |
| 12 | [huber-boosted-residual](../../configs/run-warmup20-validation-12-huber-boosted-residual.json) | -0.000880 | -0.000409 | -0.000538 |  |
| 13 | [knn-local-residual](../../configs/run-warmup20-validation-13-knn-local-residual.json) | -0.000685 | -0.000365 | -0.000494 |  |
| 14 | [monotone-lightgbm](../../configs/run-warmup20-validation-14-monotone-lightgbm.json) | +0.000447 | +0.000287 | +0.000158 |  |
| 15 | [lightgbm-l1-leaf-residual](../../configs/run-warmup20-validation-15-lightgbm-l1-leaf-residual.json) | -0.000532 | -0.000394 | -0.000523 |  |
| 16 | [asset-lead-sigmoid](../../configs/run-warmup20-validation-16-asset-lead-sigmoid.json) | +0.005119 | +0.001518 | +0.001388 |  |
| 17 | [asset-lead-beta](../../configs/run-warmup20-validation-17-asset-lead-beta.json) | -0.000364 | +0.000130 | +0.000001 |  |
| 18 | [asset-lead-isotonic](../../configs/run-warmup20-validation-18-asset-lead-isotonic.json) | -0.000304 | +0.000099 | -0.000031 |  |
| 19 | [asset-lead-temperature](../../configs/run-warmup20-validation-19-asset-lead-temperature.json) | +0.004906 | +0.001435 | +0.001305 |  |
| 20 | [hinge-logit-calibration](../../configs/run-warmup20-validation-20-hinge-logit-calibration.json) | -0.000512 | -0.000196 | -0.000325 |  |
| 21 | [shrunk-cell-intercept](../../configs/run-warmup20-validation-21-shrunk-cell-intercept.json) | +0.000344 | +0.002422 | +0.002293 |  |
| 22 | [asset-lead-sparse-residual](../../configs/run-warmup20-validation-22-asset-lead-sparse-residual.json) | -0.001099 | -0.000602 | -0.000731 | yes |
| 23 | [newton-logloss-ridge](../../configs/run-warmup20-validation-23-newton-logloss-ridge.json) | -0.000884 | -0.000587 | -0.000716 |  |
| 24 | [newton-logloss-sparse](../../configs/run-warmup20-validation-24-newton-logloss-sparse.json) | -0.001340 | -0.000775 | -0.000904 | yes |
| 25 | [variance-weighted-ridge](../../configs/run-warmup20-validation-25-variance-weighted-ridge.json) | -0.001085 | -0.000667 | -0.000796 |  |
| 26 | [variance-weighted-histogram](../../configs/run-warmup20-validation-26-variance-weighted-histogram.json) | -0.000556 | -0.000383 | -0.000513 |  |
| 27 | [fair-loss-residual](../../configs/run-warmup20-validation-27-fair-loss-residual.json) | -0.000357 | +0.001163 | +0.001034 |  |
| 28 | [absolute-loss-residual](../../configs/run-warmup20-validation-28-absolute-loss-residual.json) | -0.000343 | +0.000258 | +0.000129 |  |
| 29 | [market-soft-label-entropy](../../configs/run-warmup20-validation-29-market-soft-label-entropy.json) | +0.002568 | +0.002003 | +0.001873 |  |
| 30 | [modified-huber-classifier](../../configs/run-warmup20-validation-30-modified-huber-classifier.json) | -0.000355 | -0.000258 | -0.000387 |  |
| 31 | [elastic-net-logistic-sgd](../../configs/run-warmup20-validation-31-elastic-net-logistic-sgd.json) | +0.006586 | +0.008839 | +0.008710 |  |
| 32 | [tanh-brier-residual-network](../../configs/run-warmup20-validation-32-tanh-brier-residual-network.json) | -0.000279 | -0.000079 | -0.000208 |  |
| 33 | [weekly-refit-sparse-residual](../../configs/run-warmup20-validation-33-weekly-refit-sparse-residual.json) | -0.001167 | -0.000622 | -0.000752 |  |
| 34 | [week-embargo-sparse-residual](../../configs/run-warmup20-validation-34-week-embargo-sparse-residual.json) | -0.001167 | -0.000622 | -0.000752 |  |
| 35 | [local-regime-beta](../../configs/run-warmup20-validation-35-local-regime-beta.json) | +0.000296 | +0.000026 | -0.000104 |  |
| 36 | [penalty-matched-asset-lead-sigmoid](../../configs/run-warmup20-validation-36-penalty-matched-asset-lead-sigmoid.json) | -0.000553 | +0.000178 | +0.000048 |  |
| 37 | [penalty-matched-temperature](../../configs/run-warmup20-validation-37-penalty-matched-temperature.json) | -0.000813 | +0.000056 | -0.000073 |  |
| 38 | [bayesian-newton](../../configs/run-warmup20-validation-38-bayesian-newton.json) | -0.001294 | -0.000758 | -0.000888 | yes |
| 39 | [elastic-newton](../../configs/run-warmup20-validation-39-elastic-newton.json) | -0.001167 | -0.000723 | -0.000853 |  |
| 40 | [robust-newton](../../configs/run-warmup20-validation-40-robust-newton.json) | -0.000829 | +0.001206 | +0.001077 |  |
| 41 | [quadratic-newton](../../configs/run-warmup20-validation-41-quadratic-newton.json) | -0.000881 | -0.000591 | -0.000720 |  |
| 42 | [spline-newton](../../configs/run-warmup20-validation-42-spline-newton.json) | -0.000705 | -0.000250 | -0.000379 |  |
| 43 | [radial-newton](../../configs/run-warmup20-validation-43-radial-newton.json) | -0.000675 | -0.000244 | -0.000373 |  |
| 44 | [interaction-sparse-newton](../../configs/run-warmup20-validation-44-interaction-sparse-newton.json) | -0.001364 | -0.000776 | -0.000905 | yes |
| 45 | [market-only-newton](../../configs/run-warmup20-validation-45-market-only-newton.json) | -0.000694 | -0.000230 | -0.000359 |  |
| 46 | [no-gru-newton](../../configs/run-warmup20-validation-46-no-gru-newton.json) | -0.000607 | -0.000339 | -0.000468 |  |
| 47 | [disagreement-only-newton](../../configs/run-warmup20-validation-47-disagreement-only-newton.json) | -0.000465 | -0.000303 | -0.000432 |  |
| 48 | [volatility-expert-newton](../../configs/run-warmup20-validation-48-volatility-expert-newton.json) | -0.001252 | -0.000791 | -0.000920 | yes |
| 49 | [spread-expert-newton](../../configs/run-warmup20-validation-49-spread-expert-newton.json) | -0.001237 | -0.000724 | -0.000853 |  |
| 50 | [distance-neighbor-residual](../../configs/run-warmup20-validation-50-distance-neighbor-residual.json) | -0.000352 | +0.000027 | -0.000102 |  |
| 51 | [latent-pls-residual](../../configs/run-warmup20-validation-51-latent-pls-residual.json) | -0.000871 | -0.000528 | -0.000657 |  |
| 52 | [gaussian-naive-bayes](../../configs/run-warmup20-validation-52-gaussian-naive-bayes.json) | +0.000175 | +0.000194 | +0.000065 |  |
| 53 | [shrinkage-discriminant](../../configs/run-warmup20-validation-53-shrinkage-discriminant.json) | +0.000034 | +0.000159 | +0.000029 |  |
| 54 | [quadratic-discriminant](../../configs/run-warmup20-validation-54-quadratic-discriminant.json) | +0.005683 | +0.004374 | +0.004245 |  |
| 55 | [histogram-logloss](../../configs/run-warmup20-validation-55-histogram-logloss.json) | +0.000889 | +0.000569 | +0.000439 |  |
| 56 | [randomized-newton-trees](../../configs/run-warmup20-validation-56-randomized-newton-trees.json) | -0.000576 | -0.000345 | -0.000475 |  |
| 57 | [deep-relu-residual](../../configs/run-warmup20-validation-57-deep-relu-residual.json) | +0.000058 | +0.000012 | -0.000117 |  |

## Why the 20 new experiments were tried

- **38 bayesian-newton:** Learn one shared Bayesian coefficient precision rather than independent ARD shrinkage.
- **39 elastic-newton:** Sparse elastic-net shrinkage in market-curvature geometry tests a different feature-selection penalty.
- **40 robust-newton:** Robustify curvature-scaled residual fitting; this is a robust surrogate, not exact Newton log-loss optimization.
- **41 quadratic-newton:** Quadratic market/GRU disagreement interactions test curved local misspecification.
- **42 spline-newton:** Fixed truncated quadratic splines allow smooth probability calibration bends with no learned knot locations.
- **43 radial-newton:** Gaussian radial basis calibration localizes smooth corrections around fixed market-logit centers.
- **44 interaction-sparse-newton:** Hierarchical-style asset, confidence and volatility interactions allow shared sparse coefficients without separate tiny cells.
- **45 market-only-newton:** Ablate the GRU and all external price features to isolate market calibration as the signal source.
- **46 no-gru-newton:** Retain spot, volatility and activity while removing GRU inputs, testing whether the neural forecast adds information.
- **47 disagreement-only-newton:** Keep only GRU disagreement interactions to separate incremental forecast content from market-only calibration.
- **48 volatility-expert-newton:** Independent fits above/below a fixed volatility level test conditional calibration regimes.
- **49 spread-expert-newton:** Independent narrow/wide-spread experts test quote-quality-dependent miscalibration.
- **50 distance-neighbor-residual:** Distance-weighted local averaging emphasizes nearby probability/disagreement states instead of uniform neighbors.
- **51 latent-pls-residual:** Supervised low-rank partial least squares tests whether correlated predictors reduce to a few useful residual directions.
- **52 gaussian-naive-bayes:** A strongly anchored conditional-independence density model tests a generative alternative to discriminative fitting.
- **53 shrinkage-discriminant:** Shrinkage covariance linear discriminant analysis tests stable joint class-density information.
- **54 quadratic-discriminant:** Regularized class-specific covariance permits curved decision surfaces under conservative market anchoring.
- **55 histogram-logloss:** Direct Bernoulli log-loss boosting contrasts with the earlier squared-residual histogram models.
- **56 randomized-newton-trees:** Randomized splits on curvature-scaled predictors test nonlinear Newton-surrogate interactions with restrained corrections.
- **57 deep-relu-residual:** A compact two-layer ReLU residual network tests compositional nonlinear structure beyond the prior single tanh layer.

## Interpretation and decision

Sparse market-anchored corrections were the prior studies' strongest family, motivating alternative shrinkage, interaction bases and information ablations. Generative classifiers, supervised latent regression, local experts and deeper residual networks are separate hypotheses rather than parameter-only copies. Each proposal and configuration preceded its development run. No new method was chosen in response to this batch's validation results.

Retain method24 as the reference and method48 as one new comparison candidate; methods44 and38 offer similar results without an established incremental advantage. Stop broad architecture sweeps on this same history. The next bounded investigation should test whether candidate cells selected for stability across warm-up subperiods repeat, rather than selecting only the largest warm-up mean gain. Keep market-calibration-only opportunities separate from claims of extra information beyond recalibration. A method that improves raw market but loses to simple calibration has not shown incremental information beyond calibration. A numerical gain over method24 without its adjusted screen passing is not an established replacement. Unshortlisted validation winners remain descriptive and cannot be promoted through this analysis.

The earlier combined-period recommendations are superseded by this aligned assessment. Do not stop the project merely because a different frozen model has a future endpoint; historical development can continue under separate training/validation rules. For stronger confirmation, keep the current shortlist fixed and use genuinely unexamined outcomes. The separate protected forward protocol remains unchanged.

## Verification, failures and limits

The initial tests failed because the 114 documents did not yet exist and a synthetic regression fixture omitted its prediction-method argument. Both were corrected before model execution. Every completed pipeline is retained with its configuration identity and six writer hashes. Exact source, fit, score and aggregation audits are recorded in the evidence packet. All 114 modeling runs completed without runtime failure. The audit verified 684 output hashes/counts, all 1,971 causal training and prediction censuses, identical benchmark predictions/cohorts, five probability-score series and every aggregate field. All 238 focused tests, Ruff, and document validation passed; the full suite was not run. Two fresh independent reviews completed on candidate c505e7e1ca731c899f411819e14dcc51fba42cfa with zero Critical/Major findings. The integration review recorded one Minor provenance omission, closed by the clarification below.

The market benchmark is the inherited one-minute candle-based midpoint proxy, not a reconstructed exchange order book. Original source generation and GRU training were not rebuilt in this study.

W1/W2 were already explored in earlier work. Selection isolation within this batch does not undo that knowledge. The inherited GRU backbone updates on its original causal schedule, while all fitted correction models and benchmarks are fixed at May2 throughout validation. This is Kalshi BTC/ETH only; no Polymarket or profitability conclusion. Day-cluster inference assumes approximately independent days; seven-day sensitivity is not proof of independence. Every comparator family is corrected separately, not jointly across all four comparators.

## Reproducibility

Artifacts: /home/russell/data/crypto_trading/research/warmup20-20261018. Exact stage commands, iteration rationales/timestamps, warm-up selection lock, run identities, source hashes, package versions and complete outputs are retained. Existing output paths refuse overwrites; reruns require unused output placement.

- [Full evidence packet](2026-10-18-warmup20-evidence.json)
- [All 57 aligned methods](/home/russell/data/crypto_trading/research/warmup20-20261018/aligned-57-methods.csv)
- [Every cell with uncertainty](/home/russell/data/crypto_trading/research/warmup20-20261018/all-cell-inference.csv)
- [Per-lead summaries](/home/russell/data/crypto_trading/research/warmup20-20261018/all-leads.csv)
- [Per-lead/bin calibration](/home/russell/data/crypto_trading/research/warmup20-20261018/all-calibration.csv)
- [Chronological cell stability](/home/russell/data/crypto_trading/research/warmup20-20261018/cell-band-stability.csv)
- [Market-only gap map](/home/russell/data/crypto_trading/research/warmup20-20261018/aligned-market-gap-map.csv)
- [Frozen development selection](/home/russell/data/crypto_trading/research/warmup20-20261018/selection-lock.json)

- [Selected validation cells](/home/russell/data/crypto_trading/research/warmup20-20261018/selected-validation-cells.csv)
- [Earlier region retest](/home/russell/data/crypto_trading/research/warmup20-20261018/prior-region-aligned-retest.csv)

## Primary references

- [Chronological validation and model selection](https://scikit-learn.org/stable/modules/cross_validation.html)
- [Partial least squares](https://scikit-learn.org/stable/modules/generated/sklearn.cross_decomposition.PLSRegression.html)
- [Linear and quadratic discriminant models](https://scikit-learn.org/stable/modules/lda_qda.html)
- [Distance-weighted neighbors](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.KNeighborsRegressor.html)
- [LightGBM objectives and randomized splits](https://lightgbm.readthedocs.io/en/latest/Parameters.html)

## Review closure

Candidate c505e7e1ca731c899f411819e14dcc51fba42cfa received sequential independent correctness/statistics/causality and tests/integration/reproducibility reviews. The full reviewer reports and SHA-256 digests are retained in the evidence packet under review_closure. The first review independently verified all global/cell statistics; the second independently verified every aggregate field (16,758 aggregate-column checks). Their focused test results were 238 and 188 passes respectively.

Integration finding I-1 (Minor, midpoint-proxy provenance) is closed by the explicit source clarification above. Final unresolved counts: Critical 0, Major 0, Minor 0, Nit 0. No experimental code, configuration, test, dependency, selection rule, result or analysis script changed after review. The closure changes only provenance wording, review evidence and handoff notes. This review covers the historical research deliverable; raw source reconstruction and inherited GRU retraining remain outside its scope.
