# Twenty-two further tests of local crypto market calibration

> Recommendation update, October 18: use the [aligned warm-up/validation reassessment](2026-10-18-warmup20-aligned-methods.md). The original results below remain a record of combined-period historical exploration.

## TL;DR

**All 22 additional tests are complete: 37 methods across both batches.** The new batch has 22 runnable JSONs and 132 output files: 2,200 asset/lead/bin cells, 110 lead rows, 220 asset/lead rows and 1,100 calibration rows, plus every forecast and contract score.
Using cumulative exploratory screens, 10 new methods improve overall versus raw market, 6 versus simple recalibration, and 0 versus the previously selected sparse model. These are repeated-history findings, not fresh confirmation.

## Execution contract

- Owner requested 15–30 more approaches to miscalibration, including different models, training and losses. Base: 841f6d1f. Work ran in WSL2 across October 17-18 Eastern; as-of label October 17, 2026. Exact UTC execution times are in the iteration ledger.
- Twenty distinct approaches were declared, then two penalty-scale controls were added after early calibration results. These controls count as two additional attempts, not two new architecture families.
- Same acquired Kalshi BTC/ETH panel as batch one: 189,761 available historical rows; every method scores the identical 137,737 forecasts / 28,373 contracts / 151 active UTC days, February 19–July 31, 2026. Five leads: 2, 5, 8, 11, 14 minutes. No Polymarket claim.
- Settlement is binary. Market-implied distribution is Bernoulli with YES probability equal to contemporaneous bid/ask midpoint. Evaluation compares probability forecasts to actual YES/NO settlement, using log loss and Brier score. No trading, fees, profitability or sizing analysis.
- Benchmarks: raw market, the same per-lead logit recalibration used previously, and the exact previous pooled ARD sparse residual method 05. Both fitted benchmarks are independently retrained in every JSON; saved predictions match the old outputs.
- Candidate training expands every 30 days, except method 33 refits weekly. Actual settlement publication plus 24 hours gates training; method 34 uses seven days. Both benchmarks retain their original 30-day / one-day schedule.
- All preprocessing is fixed and causal. Optimizers may shuffle already-admitted training examples; there is no random future validation split. No protected forward outcome was read or protocol changed. Original chronological GRU forecasts remain inputs.
- Fixed market-defined ten-point bins keep cohorts identical. Every report contains model/raw-market/recalibrated-market/prior-ARD probabilities, proper scores, paired differences, actual YES rates and denominators.

## Completed methods

Negative differences favor the new method. Overall scores average available leads within each contract before averaging contracts. Per-lead and cell reports weight their forecast rows. Adjusted p-values use 37 attempts, including the first batch.

| Method / JSON | Log loss vs market | Brier vs market | Log loss vs recalibration | Log loss vs prior ARD | Adjusted p vs ARD |
|---|---:|---:|---:|---:|---:|
| [16-asset-lead-sigmoid](../../configs/run-calibration-next20-16-asset-lead-sigmoid.json) | +0.002682 | +0.000937 | +0.002903 | +0.003486 | 1 |
| [17-asset-lead-beta](../../configs/run-calibration-next20-17-asset-lead-beta.json) | -0.000094 | +0.000042 | +0.000127 | +0.000710 | 1 |
| [18-asset-lead-isotonic](../../configs/run-calibration-next20-18-asset-lead-isotonic.json) | -0.000045 | +0.000063 | +0.000176 | +0.000760 | 1 |
| [19-asset-lead-temperature](../../configs/run-calibration-next20-19-asset-lead-temperature.json) | +0.002540 | +0.000879 | +0.002761 | +0.003344 | 1 |
| [20-hinge-logit-calibration](../../configs/run-calibration-next20-20-hinge-logit-calibration.json) | -0.000307 | -0.000056 | -0.000086 | +0.000497 | 1 |
| [21-shrunk-cell-intercept](../../configs/run-calibration-next20-21-shrunk-cell-intercept.json) | +0.001284 | -0.000002 | +0.001505 | +0.002088 | 1 |
| [22-asset-lead-sparse-residual](../../configs/run-calibration-next20-22-asset-lead-sparse-residual.json) | -0.000785 | -0.000189 | -0.000564 | +0.000019 | 1 |
| [23-newton-logloss-ridge](../../configs/run-calibration-next20-23-newton-logloss-ridge.json) | -0.000684 | -0.000133 | -0.000464 | +0.000120 | 1 |
| [24-newton-logloss-sparse](../../configs/run-calibration-next20-24-newton-logloss-sparse.json) | -0.000981 | -0.000240 | -0.000760 | -0.000177 | 0.2069 |
| [25-variance-weighted-ridge](../../configs/run-calibration-next20-25-variance-weighted-ridge.json) | -0.000810 | -0.000161 | -0.000589 | -0.000006 | 1 |
| [26-variance-weighted-histogram](../../configs/run-calibration-next20-26-variance-weighted-histogram.json) | -0.000430 | -0.000098 | -0.000209 | +0.000375 | 1 |
| [27-fair-loss-residual](../../configs/run-calibration-next20-27-fair-loss-residual.json) | +0.000583 | +0.000364 | +0.000804 | +0.001387 | 1 |
| [28-absolute-loss-residual](../../configs/run-calibration-next20-28-absolute-loss-residual.json) | +0.000019 | +0.000123 | +0.000240 | +0.000823 | 1 |
| [29-market-soft-label-entropy](../../configs/run-calibration-next20-29-market-soft-label-entropy.json) | +0.002158 | +0.000162 | +0.002379 | +0.002963 | 1 |
| [30-modified-huber-classifier](../../configs/run-calibration-next20-30-modified-huber-classifier.json) | -0.000281 | -0.000044 | -0.000060 | +0.000523 | 1 |
| [31-elastic-net-logistic-sgd](../../configs/run-calibration-next20-31-elastic-net-logistic-sgd.json) | +0.005989 | +0.001145 | +0.006210 | +0.006793 | 1 |
| [32-tanh-brier-residual-network](../../configs/run-calibration-next20-32-tanh-brier-residual-network.json) | -0.000111 | -0.000012 | +0.000110 | +0.000694 | 1 |
| [33-weekly-refit-sparse-residual](../../configs/run-calibration-next20-33-weekly-refit-sparse-residual.json) | -0.000827 | -0.000203 | -0.000606 | -0.000022 | 1 |
| [34-week-embargo-sparse-residual](../../configs/run-calibration-next20-34-week-embargo-sparse-residual.json) | -0.000838 | -0.000203 | -0.000617 | -0.000034 | 0.469 |
| [35-local-regime-beta](../../configs/run-calibration-next20-35-local-regime-beta.json) | +0.000123 | +0.000049 | +0.000344 | +0.000928 | 1 |
| [36-penalty-matched-asset-lead-sigmoid](../../configs/run-calibration-next20-36-penalty-matched-asset-lead-sigmoid.json) | -0.000171 | +0.000014 | +0.000050 | +0.000633 | 1 |
| [37-penalty-matched-temperature](../../configs/run-calibration-next20-37-penalty-matched-temperature.json) | -0.000350 | -0.000051 | -0.000129 | +0.000455 | 1 |

Best new mean score: method **24**. Its log loss is 0.459574, versus market 0.460556 and prior ARD 0.459751.
Chronological raw-market log-loss differences: w1-fold14: -0.001340, w2-block1: -0.000821, w2-block2: -0.000603.

### Best new method by lead

| Lead | Forecasts | Log loss vs market | Brier vs market | Log loss vs recalibration | Log loss vs prior ARD |
|---:|---:|---:|---:|---:|---:|
| 2m | 24,603 | -0.000622 | -0.000082 | -0.001380 | -0.000462 |
| 5m | 28,066 | -0.001019 | -0.000111 | -0.000538 | -0.000184 |
| 8m | 28,343 | -0.000982 | -0.000213 | -0.000620 | -0.000045 |
| 11m | 28,359 | -0.000743 | -0.000242 | -0.000752 | -0.000091 |
| 14m | 28,366 | -0.000872 | -0.000379 | -0.001000 | -0.000102 |

## Where miscalibration appears

The market-only map asks a separate question: within each of the 100 fixed asset/lead/bin regions, how far is actual YES frequency from mean market probability? Positive gaps mean more YES settlements than the market implies. Two-sided day-cluster tests are adjusted across those 100 regions; seven-day blocks are a sensitivity check. This is descriptive repeated-history evidence.

| Asset / lead / market bin | Contracts | Market YES | Actual YES | Gap, percentage points | Adjusted p | Weekly adjusted p |
|---|---:|---:|---:|---:|---:|---:|
| ETH / 5m / 90-100% | 3,068 | 95.77% | 97.23% | +1.46 | 7.457e-05 | 0.004525 |
| ETH / 8m / 90-100% | 1,516 | 94.07% | 96.50% | +2.44 | 8.997e-05 | 6.35e-05 |
| BTC / 5m / 90-100% | 3,147 | 95.67% | 96.70% | +1.03 | 0.01786 | 0.4149 |
| ETH / 5m / 10-20% | 1,364 | 14.64% | 12.10% | -2.55 | 0.1111 | 0.4523 |
| ETH / 5m / 30-40% | 829 | 34.59% | 29.92% | -4.68 | 0.3233 | 1 |
| ETH / 2m / 00-10% | 4,346 | 2.12% | 1.59% | -0.53 | 0.6869 | 1 |
| ETH / 11m / 70-80% | 1,646 | 74.51% | 77.10% | +2.59 | 0.995 | 1 |
| BTC / 2m / 00-10% | 4,461 | 2.18% | 2.06% | -0.11 | 1 | 1 |

Eight eligible cells ordered by adjusted p-value are shown for inspection; they do not define a retrospectively selected combined predictor. All 100 regions are saved in market-miscalibration-map.csv. A calibration gap alone does not establish that a fitted correction improves future scores.

New method/cell combinations passing the proper-score screen: **14 versus raw market; 0 versus recalibration; 0 versus prior ARD**. The 3,700-cell adjustment includes both batches, even though only the new batch has the prior-ARD comparator.

### Strongest eligible cell improvements

| Method / asset / lead / bin | Contracts | Actual YES | Market YES | Model YES | Loss vs market | Loss vs recalibration | Loss vs prior ARD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 24 / ETH / 5m / 10-20% | 1,364 | 12.10% | 14.64% | 13.79% | -0.003331 | -0.000615 | -0.001454 |
| 20 / ETH / 8m / 90-100% | 1,516 | 96.50% | 94.07% | 94.77% | -0.003066 | +0.000854 | -0.000410 |
| 20 / ETH / 5m / 90-100% | 3,068 | 97.23% | 95.77% | 96.31% | -0.001877 | +0.000757 | -0.000265 |

Only 4 of the 14 raw-market method/cell combinations also clear the seven-day sensitivity. The displayed method-24 ETH 5m / 10-20% cell does not: weekly adjusted p=0.2387. These cells are post-inspection examples. Their per-band results, all losing cells and seven-day sensitivity are retained in the evidence. No cell-level correction rule was selected using its own scored outcomes.

## What the iterations tested

- Methods 16–20 change calibration shape and sharing: separate asset/lead sigmoid, asymmetric beta, isotonic, temperature-only and fixed logit hinges.
- Method 21 fits an intercept in each asset/lead/decile, with a ridge prior equivalent to 500 zero-residual observations. Method 22 specializes sparse residual fits by asset and lead.
- Methods 23–24 approximate a single regularized Newton step for log loss around market probability. With v=max(p(1-p),1e-4), train on (y-p)/sqrt(v) using sqrt(v) times each feature, including an explicit constant. Divide the fitted output by sqrt(v), cap the logit correction to [-1,1], shrink by 0.5 and apply the logistic link. This is a local quadratic approximation, not a claim of exact log-loss optimization.
- Methods 25–26 fit standardized residuals with ridge or histogram boosting, then restore probability units with sqrt(v). These emphasize error relative to implied uncertainty.
- Methods 27–32 change objective/architecture: Fair loss, absolute residual loss, cross entropy on market-softened labels, modified Huber classification, averaged elastic-net logistic SGD and a small tanh residual network. Absolute loss targets a median, not the Bernoulli mean; it is a deliberate stress test, not assumed probability-consistent.
- Methods 33–34 test weekly refitting and a longer label embargo. Methods 36–37 are explicit controls: dividing logits by four with unchanged C had strengthened effective slope regularization sixteenfold in methods 16/19. C=0.8 restores the raw-logit slope penalty corresponding to C=0.05. No earlier losing method was replaced.
- Every launch records the preceding score differences and the next distinct hypothesis in iterations.jsonl and JSON notes. No earlier losing method was replaced.

## Uncertainty and limits

Global differences first average leads within contracts, then use day-cluster standard errors. For contract differences d_i, N contracts, D days and S_d the sum of demeaned differences in day d:

```math
\mathrm{SE}=\frac{\sqrt{\frac{D}{D-1}\sum_d S_d^2}}{N}.
```

Best new method versus prior ARD: mean -0.000176988, SE 0.000068903, N=28,373, D=151; one-sided p=0.00559287, adjusted p=0.206936. Its seven-day adjusted p is 0.111472, also above .05.

- Each raw/calibrated/prior-ARD comparison has its own 37-method global family and 3,700-cell family, alpha .05; this does not control one combined error budget across all benchmark families. Cells additionally need 200 contracts, 20 days and negative Brier differences versus the same comparator.
- Bonferroni handles dependence across tests, not adaptive reuse of outcomes. Days are approximately independent clusters; seven-day aggregation checks sensitivity but cannot certify independence.
- The market-only gap map is a separate fixed 100-cell, two-sided descriptive family. It does not select a trading policy or establish out-of-sample score improvement.
- The old ARD model was itself chosen on this history. Last-band results and repeated chronological bands are stability diagnostics, not untouched confirmation. First-batch and interim 35-method screens are superseded here by 37/3,700 adjustments.
- Midpoint is a contemporaneous one-minute candle proxy. Original data exclusions and source timestamps remain those documented in batch one. No new source coverage or Polymarket data was acquired.

## Decision and next step

**Keep the prediction project alive for independent confirmation; retain method 24 as one research challenger.** Its mean log loss is about 0.213% below raw market, but the improvement over the prior sparse model does not clear the cumulative screen. It is not an established replacement.

The strongest calibration evidence is ETH at 5 and 8 minutes in the 90-100% market bins: both market-only gaps survive the day and seven-day checks. BTC at 5 minutes survives the day check but not the weekly sensitivity. ETH 5m / 10-20% is interesting for model 24, but its local improvement is not robust to the full uncertainty checks.

The next useful test is fresh-data prediction, with the existing frozen January 15, 2027 UTC confirmation left unchanged. If that protocol succeeds, separately preregister one method-24 versus method-05 versus market-calibration comparison on a new untouched interval, with these regions fixed before outcomes are read. If the existing predictive protocol fails its own criterion, stop this modeling line rather than recycle these cells. No new future experiment or automation was started.

## Failures, checks and handoff

The first method-16 fit completed, but the writer refused a missing output directory. A same-identity retry was then refused. Both attempts are retained; completed outputs use runs-final. No output was overwritten, and only complete runs count. The initial tail-test fixture lacked required identifier fields and was corrected before model execution.

Verification covers all 22 current document/run identities, all 132 file hashes/counts, identical row cohorts and benchmark predictions, every causal fit census, proper-score arithmetic and every aggregate view. 236 focused tests and Ruff passed; the full suite was not run. Two fresh independent reviews passed with zero findings; receipts follow below.

## Reproducibility

[Full evidence packet](2026-10-17-calibration-next20-evidence.json). Artifacts: /home/russell/data/crypto_trading/research/calibration-next20-20261017.

- [All new cells](/home/russell/data/crypto_trading/research/calibration-next20-20261017/all-cells.csv)
- [Lead summaries](/home/russell/data/crypto_trading/research/calibration-next20-20261017/all-leads.csv)
- [Calibration rows](/home/russell/data/crypto_trading/research/calibration-next20-20261017/all-calibration.csv)
- [Market miscalibration map](/home/russell/data/crypto_trading/research/calibration-next20-20261017/market-miscalibration-map.csv)
- [All 37 methods](/home/russell/data/crypto_trading/research/calibration-next20-20261017/all-37-methods.csv)

Use the project Python environment in WSL and run a selected JSON from the crypto child. Reproduction requires the same onboarded panel and pinned package versions. Existing output locations intentionally refuse reuse; choose a new run root and unused writer paths. Exact commands, configurations, source hashes, training records and scripts are retained.

## Primary method references

- [Beta calibration](https://proceedings.mlr.press/v54/kull17a.html): asymmetric probability calibration.

- [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html): sigmoid/isotonic and proper-score interpretation.
- [Linear models](https://scikit-learn.org/stable/modules/linear_model.html): ridge, ARD, regularization and weighted least squares.
- [SGDClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.SGDClassifier.html): modified Huber and logistic probability behavior.
- [MLPRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.neural_network.MLPRegressor.html): squared-error neural fitting.
- [LightGBM parameters](https://lightgbm.readthedocs.io/en/latest/Parameters.html): Fair, absolute and cross-entropy objectives.

## Review closure

Candidate 89ca6c39f06f3f3a6bff722d7f85588a733295f0 passed two sequential fresh GPT-6 skeptic reviews: correctness/statistics/causality and integration/reproducibility. Both report zero Critical, Major, Minor or Nit findings. The evidence packet retains both complete reports and their SHA256 digests.

Together they independently checked all 132 outputs, 2,081 causal fit records, cohort and benchmark equality, score and aggregation arithmetic, inference adjustments and the market-gap map. The second reviewer also reran all 236 focused tests; Ruff passed. The full suite and model training were not rerun during review. These reviews validate the bounded historical research and retained results, not future predictive advantage.

Closure changes only this receipt, the evidence receipt and re-entry notes. Reviewed configurations, tests, dependencies, journal, source and numerical results remain unchanged.
