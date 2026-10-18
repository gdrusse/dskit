# Fifteen methods for predicting crypto settlement better than the market

> Recommendation update, October 18: use the [aligned warm-up/validation reassessment](2026-10-18-warmup20-aligned-methods.md). The original results below remain a record of combined-period historical exploration.

## TL;DR

**All 15 methods are complete:** each has a runnable JSON, chronological training, forecast scores, 100 asset/lead/bin cells, and separate lead and calibration outputs. Four improve on a fitted market-only calibration benchmark overall in historical development data; no cell establishes an incremental improvement beyond that benchmark after the 1,500-cell screen.

**Recommendation: stop broad model searching, but do not stop the prediction project yet.** Preserve the existing untouched predictive confirmation test. The new sparse model is a research candidate, not a demonstrated replacement for the previously frozen GRU hybrid/lead-hierarchy model.

## Execution contract

- Owner request: October 17, 2026, including at least 15 complete JSON/output sets. Executed in WSL2 on October 17 Eastern / October 18 UTC. Base commit: 834f2c47.
- Venue: Kalshi BTC/ETH 15-minute Up/Down contracts. No Polymarket claim, trading rule, fees, profit, sizing or execution evaluation.
- Target: probability of YES settlement; NO is one minus YES. Benchmark: contemporaneous YES bid/ask midpoint. Second benchmark: per-lead logistic recalibration of midpoint, fitted on the identical causal schedule.
- Fixed leads: 2, 5, 8, 11 and 14 minutes. The acquired common panel has 189,761 rows over 40,090 contracts. Every final method scores the **same 137,737 forecasts from 28,373 contracts over 151 active UTC days**, February 19–July 31, 2026.
- Training begins with the earlier December/January/February data and expands every 30 days. Labels enter training only when actual settlement-publication time plus 24 hours is strictly before the fitting cutoff. Original GRU predictions retain their strict chronological source-band restrictions.
- Features: market probability, causal GRU disagreement, lead, asset, confidence, spread, volatility-normalized moneyness, volatility level/ratio, volume/open interest and lead interactions. Each method selects its declared subset; ARD and elastic net learn sparse coefficients inside each training block. The existing GRU is an input, not a newly retrained neural network.
- No shuffled split, random early-stopping holdout, or globally fitted preprocessing. W4/W5 tables were excluded from the new fits/scores because their derived table omits settlement-publication timestamps. Prior W4/W5 reports informed design. Protected forward data and frozen protocols were unchanged.
- Calibration uses **market-defined ten-percentage-point bins**, preserving identical cohorts between models. Outputs show mean model, raw-market and calibrated-market probabilities, actual YES frequency, log loss, Brier score, paired differences and row/contract/day counts.

## What prior research changed

The 66-lane neural zoo and later checks showed a modest GRU improvement over unconditional return forecasts, but the raw GRU generally lost to market probabilities. Later residual studies favored small market-anchored corrections and lead sharing; larger MLPs, stacks and online adaptation were unhelpful. Continuous-return mean models were weak. These 15 new approaches therefore test calibration, shrinkage, sparse selection, and restrained local nonlinear corrections.

Reviewed: [W1 zoo](2026-10-11-zoo-w1-selection-and-model-pick.md), [W2 check](2026-10-11-w2-predictor-check.md), [W4/W5 audit](2026-10-12-w5-audit-and-forward-setup.md), and the October 16 market-residual / October 17 continuous-distribution research in the separate prew4 worktree. That dirty checkout was inspected without modification.

## Fifteen completed methods

Negative differences favor the model. Log loss uses natural logarithms; Brier is squared probability error. Adjusted p-values below are **exploratory within-batch screens**, not confirmation on untouched data.

| Method / JSON | Log-loss difference vs market | Brier difference vs market | Log-loss difference vs calibrated market | Adjusted p vs calibrated market |
|---|---:|---:|---:|---:|
| [01 Beta calibration](../../configs/run-predictability15-01-beta-calibration.json) | -0.000183 | -0.000000 | +0.000038 | 1 |
| [02 Isotonic calibration](../../configs/run-predictability15-02-isotonic-calibration.json) | -0.000212 | -0.000018 | +0.000009 | 1 |
| [03 Ridge probability residual](../../configs/run-predictability15-03-ridge-brier-residual.json) | +0.000343 | -0.000140 | +0.000564 | 1 |
| [04 Bayesian ridge residual](../../configs/run-predictability15-04-bayesian-ridge-residual.json) | -0.000753 | -0.000176 | -0.000532 | 0.0204 |
| [05 ARD sparse residual](../../configs/run-predictability15-05-ard-sparse-residual.json) | -0.000804 | -0.000192 | -0.000583 | 0.01139 |
| [06 Elastic-net residual](../../configs/run-predictability15-06-elastic-net-residual.json) | -0.000781 | -0.000159 | -0.000560 | 0.01005 |
| [07 Huber linear residual](../../configs/run-predictability15-07-huber-linear-residual.json) | -0.000508 | -0.000132 | -0.000288 | 0.1174 |
| [08 Shallow tree residual](../../configs/run-predictability15-08-shallow-tree-residual.json) | -0.000477 | -0.000118 | -0.000256 | 1 |
| [09 Random-forest residual](../../configs/run-predictability15-09-random-forest-residual.json) | -0.000477 | -0.000105 | -0.000256 | 0.9291 |
| [10 Extra-trees residual](../../configs/run-predictability15-10-extra-trees-residual.json) | -0.000317 | -0.000055 | -0.000096 | 1 |
| [11 Histogram boosting residual](../../configs/run-predictability15-11-histogram-brier-residual.json) | -0.000423 | -0.000088 | -0.000202 | 1 |
| [12 Huber boosting residual](../../configs/run-predictability15-12-huber-boosted-residual.json) | -0.000589 | -0.000123 | -0.000368 | 0.01758 |
| [13 Nearest-neighbor residual](../../configs/run-predictability15-13-knn-local-residual.json) | -0.000476 | -0.000093 | -0.000255 | 0.7256 |
| [14 Monotone LightGBM classifier](../../configs/run-predictability15-14-monotone-lightgbm.json) | +0.000340 | -0.000001 | +0.000561 | 1 |
| [15 L1/L2 LightGBM residual](../../configs/run-predictability15-15-lightgbm-l1-leaf-residual.json) | -0.000431 | -0.000091 | -0.000210 | 1 |

All methods are retained, including losses. Eleven clear the raw-market global screen with better Brier means; only **04, 05, 06 and 12** clear the calibrated-market comparison. Those four also clear that comparison using fixed seven-day clusters.

**Best new method: 05, ARD sparse residual.** Log loss is 0.459751 versus 0.460556 raw market and 0.460335 calibrated market: **0.175%** lower than raw market. Brier is 0.153945 versus 0.154137. Its raw-market loss differences are -0.001167, -0.000627 and -0.000451 in successive historical evaluation bands: persistent, but shrinking.

This does not establish superiority to the earlier frozen hybrid or lead-hierarchy challenger: their frozen states were trained on these pre-August rows, so scoring them here would be an invalid in-sample head-to-head.

### Best new method by lead

| Lead, minutes | Forecasts | Log-loss difference vs market | Brier difference vs market | Log-loss difference vs calibrated market |
|---:|---:|---:|---:|---:|
| 2 | 24,603 | -0.000160 | -0.000008 | -0.000918 |
| 5 | 28,066 | -0.000834 | -0.000086 | -0.000354 |
| 8 | 28,343 | -0.000937 | -0.000184 | -0.000575 |
| 11 | 28,359 | -0.000653 | -0.000228 | -0.000661 |
| 14 | 28,366 | -0.000770 | -0.000327 | -0.000897 |

Within-lead summaries weight forecasts. Overall scores first average available leads within each contract; the denominators deliberately differ.

## Where the cell results are

The raw-market screen retains **21 method/cell combinations, representing only three distinct ETH regions**. Ten combinations also clear the seven-day sensitivity. **Zero cells** clear the incremental screen against calibrated market. The strongest raw-market example in each region is below; these post-inspection examples do not define a combined predictor.

| ETH lead / market bin | Method | Contracts | Market YES | Actual YES | Model YES | Calibrated market YES | Loss difference vs market | vs calibrated market |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 5m / 10-20% | 06 | 1,364 | 14.64% | 12.10% | 14.34% | 12.26% | -0.001914 | +0.000802 |
| 5m / 90-100% | 12 | 3,068 | 95.77% | 97.23% | 96.30% | 96.68% | -0.002028 | +0.000605 |
| 8m / 90-100% | 12 | 1,516 | 94.07% | 96.50% | 94.73% | 95.04% | -0.003108 | +0.000813 |

At ETH 5 minutes in the 10–20% bin, YES settles less often than market probability implies. In the two high-probability regions it settles more often. Simple recalibration captures these differences better than the displayed new models. The sparse model's incremental overall improvement is distributed more broadly; this study cannot confidently localize it to one cell.

## Iteration logic and failures

Every JSON's fit notes and the retained timestamped iteration ledger record its launch rationale.

- Beta calibration did not beat simple logit calibration; isotonic then tested a different probability shape.
- Plain ridge improved Brier but damaged tail log loss. Later residual methods bound the fully corrected probability before blending with market. Bayesian, ARD and elastic net then tested different regularization and feature-selection mechanisms. Architecture and boundary treatment changed together at method 04; this study cannot separate their contributions.
- Robust linear fitting was weaker. Shallow trees, forests, randomized thresholds and histogram boosting tested local structure. Most reproduced calibration gains; robust nonlinear boosting added a modest incremental gain.
- Nearest-neighbor averaging corroborated small local corrections. Direct monotone classification lost in every band; the final method returned to penalized residual fitting.

Initial preparation failed on missing W4/W5 settlement times; the entire file was excluded. The initial method-03 pipeline emitted zero forecasts because two expressions referenced fields created in the same node. That attempt is **invalid despite exit code 0**, retained and excluded. Direct input expressions and exact output-count guards fixed it. Regression tests initially showed two expected failures in earlier configurations, then passed after guards were added. Technical/reporting reruns of earlier methods preserved paired means and row identities exactly.

## Uncertainty and interpretation

Average each contract's available lead losses first. For a paired difference d_i, N is contracts, D is UTC days, and S_d is the sum of demeaned differences within day d:

```math
\mathrm{SE}=\frac{\sqrt{\frac{D}{D-1}\sum_d S_d^2}}{N}.
```

Method 05 versus calibrated market: N = 28,373; D = 151; mean = -0.000583314; standard error = 0.000180567; t = -3.230. The raw one-sided t-approximation p-value is 0.000759; multiplying by 15 gives 0.011392. The seven-day adjusted value is 0.014466.

Global comparisons use 15-method families. The cell family is 15 × 2 assets × 5 leads × 10 bins = 1,500, including underpowered cells. Alpha is .05 per reported family; Bonferroni allows arbitrary dependence across methods/cells. Candidate cells need at least 200 contracts, 20 active days and a negative Brier difference. Day-cluster t approximations assume independent days; fixed seven-day blocks are a sensitivity, not proof that serial dependence is absent.

**These remain exploratory.** The periods were explored before, and later methods used earlier results. Within-batch adjustment does not undo adaptive history or account for all prior searches. Midpoint is a one-minute candle-based proxy for decision-time market probability. No result proves future superiority.

## Decision: where to go next

**Stop expanding the model zoo. Continue only the existing frozen, untouched predictive confirmation.** There is enough historical evidence to keep the prediction hypothesis alive, but not to justify another broad search or replace the incumbent.

Keep the October 16 preregistration unchanged: its one-read endpoint is **January 15, 2027 UTC**, with hybrid and lead-hierarchy challenger already frozen. This study neither changes that test nor consumes its outcomes. If it fails its existing predictive criterion, stop this modeling line rather than recycle historical cells. If it passes, test whether one frozen ARD residual challenger adds accuracy beyond calibrated market and the successful incumbent on a separately declared untouched interval. No future test or automation was started here.

## Reproducibility and handoff

The [portable evidence packet](2026-10-17-predictability15-evidence.json) contains all configurations/run identities, sources, hashes, fit counts, coefficients, uncertainty results, every cell and six output paths per method.

Full artifacts: /home/russell/data/crypto_trading/research/predictability15-20261017

- [1,500 cell comparisons](/home/russell/data/crypto_trading/research/predictability15-20261017/all-cells.csv)
- [75 lead summaries](/home/russell/data/crypto_trading/research/predictability15-20261017/all-leads.csv)
- [150 asset/lead summaries](/home/russell/data/crypto_trading/research/predictability15-20261017/all-asset_leads.csv)
- [750 calibration rows](/home/russell/data/crypto_trading/research/predictability15-20261017/all-calibration.csv)
- [Cell uncertainty](/home/russell/data/crypto_trading/research/predictability15-20261017/all-cell-inference.csv)

Each method retains forecast/contract scores, fit records, resolved configuration, source fingerprint, log and completion record. Final runs contain 138 candidate fits and 450 market-calibration fits; technical reruns are separate.

From the crypto child in WSL, run the chosen JSON with the project environment:
```bash
PYTHONPATH=../.. /home/russell/dskit/.venv/bin/python -m dskit.pipeline run configs/run-predictability15-05-ard-sparse-residual.json --asof 2026-10-17
```
The two source registration JSONs, retained preparation script and manifest reproduce the bounded panel. Existing runs intentionally refuse overwrite; choose a new output run root and unused writer locations for another execution.

Verification: all 15 run/document identities, all 90 output hashes/counts, identical scored rows and calibration predictions, every training cutoff/count, independent row-loss calculations and cell means checked. **167 focused tests passed; Ruff passed.** The full suite was not run. Package versions and source hashes are retained. Two independent final reviews passed; see the closure receipt below.

## Method sources

- [Kull, Silva Filho and Flach: beta calibration](https://proceedings.mlr.press/v54/kull17a.html).
- [scikit-learn: calibration and proper scores](https://scikit-learn.org/stable/modules/calibration.html).
- [scikit-learn: linear, Bayesian, sparse and robust models](https://scikit-learn.org/stable/modules/linear_model.html).
- [scikit-learn: forests and boosting](https://scikit-learn.org/stable/modules/ensemble).
- [scikit-learn: nearest neighbors](https://scikit-learn.org/stable/modules/neighbors.html).
- [LightGBM: monotonic constraints and penalties](https://lightgbm.readthedocs.io/en/latest/Parameters.html).


## Closure receipt

Reviewed candidate: f6857cb031f8e0d5b866012567e823a99c8a9726, based on 834f2c47.
Two fresh GPT-6 reviewers independently covered correctness/statistics/authority
and tests/integration/reproducibility. Both reported zero Critical, Major, Minor
and Nit findings. Actual reports, identities, commands and coverage limits are
embedded in the evidence packet; local originals are review-correctness.md and
review-integration.md in the artifact directory.

The second reviewer independently verified all 90 outputs, all grouped views,
common forecast cohorts, causal fit counts, run identities and global statistics,
and reran the 167 focused tests successfully. No full suite or model rerun was
needed. Post-review edits only record this evidence and update the handoff:
model/source configurations, tests, dependencies, scientific results and the
statistical contract remain unchanged from the reviewed candidate.

The completed study is ready for the authorized repository wrap. Retain local
data and research scripts for reproduction; the evidence packet alone does not
contain the full source dataset. The next research action is the existing frozen
confirmation read, not another historical search.
