# Market versus model: training, validation, and the ETH pocket

## TL;DR

Yes: setting 03 has better average probability scores than the market in the ETH / 11-minute / market YES 10–20% pocket during May 2–July 31, 2026: log loss is **0.400824 versus 0.404722**, a **0.96% reduction**, over **689 contracts**. The pocket was selected using February 19–April 19 warm-up predictions, and the final correction was fitted using available settlements from December 15–April 19. This is promising historical evidence, but May–July had already been examined during earlier research and the pocket does not pass the subsequent multiple-comparison uncertainty screen.

This explanation separates the data that fitted the models, selected the pocket, and measured later performance. All dates are UTC. These results concern **Kalshi BTC and ETH 15-minute contracts**; they do not establish anything about Polymarket.

## What “our model” means

- **Method 24**, our overall reference: a regularized correction to the market probability, with correction strength 0.50.
- **Setting 03**, the selected pocket candidate: the same fitted coefficients, with correction strength **0.75**. This is the version behind the result above.

Both are fitted jointly across BTC and ETH, leads of 2, 5, 8, 11 and 14 minutes, and the available market probability range. Neither is fitted only to the selected pocket.

The pocket rule filters predictions for evaluation: **asset = ETH; lead = 11 minutes before contract close; raw market YES probability at least 10% and strictly below 20%**. Membership uses the market probability, not our model's probability. There is no additional spread filter or trading decision.

## Which windows fitted and tested the correction?

The correction uses expanding history. A training row must have a decision time before the fit cutoff, and its settlement timestamp plus **one full day** must also be strictly before that cutoff.

1. **February 19 fit → February 19–March 20 warm-up predictions.** Actual training settlements span December 15, 2025 at 21:01:43.308 through February 17, 2026 at 23:46:38.075. There are **51,124 training forecasts / 11,523 contracts**, followed by **25,406 scored forecasts**.
2. **March 21 fit → March 21–April 19 warm-up predictions.** Actual training settlements span the same December 15 start through March 19 at 23:45:10.807. There are **76,488 training forecasts / 17,101 contracts**, followed by **27,593 scored forecasts**.
3. **May 2 fit → May 2–July 31 validation predictions.** Actual training settlements span December 15 through **April 19 at 23:45:14.925**. There are **105,023 training forecasts / 22,930 contracts**, followed by **84,728 scored forecasts / 17,158 contracts**.

The May 2 configuration permits settlements before May 1, but this acquired decision panel has a gap from April 20 through May 1. **April 19 is the actual last training outcome.** The configured read begins December 10; the first eligible record actually occurs December 15.

The warm-up blocks total **52,999 forecasts / 11,213 contracts**. They are out of sample relative to their correction fit, but are used to choose settings and pockets. The later May 2 fit legitimately includes those earlier warm-up outcomes in its expanding training history.

The correction and fitted market-calibration comparators remain fixed throughout May–July. The underlying GRU input changes on July 1 under its inherited schedule. The entire forecasting system therefore was not frozen for all three months.

## Training losses and later probability scores

Model and market are scored on **identical rows and outcomes**. Lower log loss and Brier score are better.

- **Log loss** measures probability assigned to the outcome that occurred; confident mistakes receive large penalties. Units are natural-log units, or nats.
- **Brier score** averages squared differences between predicted YES probability and settlement, where YES is 1 and NO is 0.
- **Accuracy** predicts YES at 50% or above, then counts correct classifications. It discards probability detail.
- **Mean probability versus realized YES rate** measures average calibration within the group. It does not establish which individual contracts were better predicted.

Global scores average available leads within each contract, then weight each contract equally. Pocket scores have one forecast per contract. The correction's fitting objective instead weights each forecast row equally.

### Reconstructed training scores: setting 03

The original correction artifacts retain fitted coefficients, not an iteration-loss trace. These are **reconstructed in-sample scores on each fit's own training data**, without fitting again:

- **February 19, full training cohort:** market log loss **0.489053**, model **0.483771**; market Brier **0.163266**, model **0.161636**.
- **March 21, full training cohort:** market log loss **0.486991**, model **0.482762**; market Brier **0.162659**, model **0.161380**.
- **May 2, full training cohort:** market log loss **0.479107**, model **0.475748**; market Brier **0.160137**, model **0.159138**.
- **May 2, training rows in the pocket:** 856 contracts; market log loss **0.415302**, model **0.412110**; market Brier **0.124948**, model **0.124159**.

These are fit diagnostics, not evidence of generalization. Absolute losses across training and later periods differ partly because the contracts and outcome mix changed. Compare model against market within each period.

### Out-of-sample pocket scores

**Warm-up, February 19–April 19: 436 contracts.**

- Raw market: log loss **0.432365**, Brier **0.132111**.
- Method 24: log loss **0.428709**, Brier **0.131225**.
- Setting 03: log loss **0.427594**, Brier **0.130957**.
- Logistic-calibrated market: log loss **0.433745**, Brier **0.132508**.
- Market mean YES **15.19%**; setting 03 mean **14.07%**; actual YES **15.83%**. Both have **84.17%** accuracy.

**Validation, May 2–July 31: 689 contracts, including 98 YES settlements.**

- Raw market: log loss **0.404722**, Brier **0.121052**.
- Method 24: log loss **0.401796**, Brier **0.120278**.
- Setting 03: log loss **0.400824**, Brier **0.120034**.
- Logistic-calibrated market: log loss **0.404055**, Brier **0.120920**.
- Market mean YES **15.41%**; setting 03 mean **14.68%**; actual YES **14.22%**. Both have **85.78%** accuracy.

Actual improvement arithmetic: subtract model loss from market loss,
**0.404722118 − 0.400823545 = 0.003898573**.
Divide by the market loss: **0.003898573 / 0.404722118 = 0.0096327**.
Multiply by 100: **0.96327% lower log loss**.
This is not a 0.96 percentage-point increase in accuracy. Both predictors stay below 50% in this pocket and classify every contract as NO.

Setting 03 improves mean log loss and Brier in every validation month. Log-loss differences, **model minus market**, are **−0.005676 in May** (206 contracts), **−0.002061 in June** (268), and **−0.004485 in July** (215). Negative favors the model.

Across the **whole validation cohort**, setting 03's log loss is **0.454767 versus 0.455526**, only a **0.1665%** reduction. Method 24 is marginally better globally at **0.454751**; setting 03 is a pocket candidate, not an overall replacement.

Warm-up illustrates why average calibration is insufficient: setting 03's mean probability was farther from the realized rate than the market's mean, yet its log loss and Brier were better. Individual probability adjustments can help even when group-average calibration is worse.

The latest attribution diagnostic gives pocket-versus-raw adjusted one-sided p-values of about **0.219 with day blocks** and **0.157 with week blocks**, after nine-comparison correction. Neither clears 0.05. This remains a candidate pocket, not an established prediction edge.

## Which data selected ETH / 11 minutes / 10–20%?

The local selection program used **February 19–April 19 out-of-sample warm-up forecasts**, not training-fit scores. It scanned **20 settings × 100 cells**: 2 assets × 5 leads × 10 raw-market probability bins, or 2,000 setting/cell candidates.

A cell needed at least 200 observations over 20 days, plus 30 observations over 5 days in each 15-day quarter. It had to improve log loss against **both raw and logistic-calibrated market** in both 30-day halves and at least three of four quarters. Full-period Brier could not worsen against either comparator.

Ranking used the worse half's loss difference against calibrated market, then the full-period difference and deterministic tie-breakers. Up to two distinct cells per parent family were retained, using the best-ranked setting for a cell. Eight model/cell pairs were frozen, including setting 03 here.

For this pocket:

- **February 19–March 5:** 125 contracts; log-loss difference vs raw **−0.008461**.
- **March 6–March 20:** 96 contracts; difference **−0.001019**.
- **March 21–April 4:** 109 contracts; difference **−0.008512**.
- **April 5–April 19:** 106 contracts; difference **+0.000029**, slightly worse than raw market, although still better than calibrated market.

It qualified under the **three-of-four** rule; it did not win every quarter. Selection halves were February 19–March 20 and March 21–April 19. Validation halves were May 2–June 15 and June 16–July 31.

The lock was written **October 18, 2026 at 14:22:34 UTC**, associated with checkpoint **ba43561f**, before this tuning study scored validation. This was a retrospective research lock, not a rule available in February.

**Earlier research had already examined May–July**, including this same ETH/11-minute/10–20% region under method 22 in the aligned 57-method study. Parent choices and GRU architecture also came from earlier selection. The local script used warm-up only, but the overall workflow was not blind to validation. A later freeze cannot undo prior exposure. The later narrow-spread finding was descriptive and was not added to the rule.

## How the model is trained

The system has two layers: an inherited price-distribution model, then a small market correction.

### Layer 1: GRU return distribution

A gated recurrent unit (**GRU**) is a neural network that reads a sequence and retains information about recent changes. This model reads **60 one-minute bars**, including returns, log volume and taker share, plus volatility/clock context and asset identity.

One GRU layer has 64 hidden units, followed by 32- and 16-unit layers with ReLU activation and 0.10 dropout. It predicts three-Gaussian mixtures for volatility-normalized returns at 15 horizons, from 1 to 15 minutes. That distribution is mapped to the probability of crossing the contract strike using basis-adjusted spot.

Training minimizes equally weighted **CRPS + tail CRPS**. CRPS scores a continuous return distribution; the tail term emphasizes standardized-return intervals [−12, −2] and [2, 12]. These losses differ from binary log loss and Brier.

Training uses AdamW, learning rate 0.0003, weight decay 0.01, batch size 512, seed 11, and a ceiling of 500 epochs. Early stopping occurs after 10 epochs without improvement on an earlier internal calibration band. The best checkpoint is retained.

Three saved states supply the studied forecasts:

- **February 19–April 19 forecasts:** gradient-fitting labels finish before **January 19**; internal calibration **January 19–February 17**. Best epoch 14 of 24: training loss **2.273331**, calibration loss **2.252783**.
- **May 2–June 30 forecasts:** gradient-fitting labels finish before **April 1**; internal calibration **April 1–April 30**. Best epoch 10 of 20: training loss **2.260279**, calibration loss **2.239636**.
- **July 1–July 31 forecasts:** gradient-fitting labels finish before **May 31**; internal calibration **May 31–June 29**. Best epoch 14 of 24: training loss **2.256699**, calibration loss **2.210312**.

Underlying history begins June 20, 2023 according to the retained W1 record. This explanation verifies saved boundary metadata and curves; it does not reload the neural training panel to determine the first eligible row or actual row counts. The first state's sampling stride is 5; later states use 1. Eligibility bands do not guarantee a row for every minute.

The GRU log field **val_loss** means the earlier internal calibration band used for early stopping. **It is not the May–July market-comparison validation score.** The original epoch curves are displayed separately. A binary market YES probability cannot supply a comparable continuous-return CRPS curve.

### Layer 2: market-anchored ARD correction

**ARD**, automatic relevance determination, is Bayesian linear regression with feature-specific shrinkage. Less useful coefficients can become zero. It learns a restrained correction rather than replacing market probability.

Let **p** be market YES probability, **q** GRU YES probability, **y** settlement (1 or 0), **L** lead minutes, and **x** the feature vector. Logit means log odds: log(p/(1−p)). There are 14 input terms:

1. Constant 1.
2. logit(p) / 4.
3. [logit(q) − logit(p)] / 4.
4. L / 14.
5. ETH indicator.
6. abs(p − 0.5).
7. Bid/ask spread in probability units.
8. Log moneyness / [60-minute RMS volatility × sqrt(60L)].
9. log(60-minute RMS volatility / 0.0001).
10. log(EWMA volatility with a 30-minute half-life (300-minute lookback) / 60-minute RMS volatility).
11. log(1 + candle volume) / 10.
12. log(1 + candle open interest) / 10.
13. Term 3 × term 4.
14. Term 2 × term 4.

Volatilities are log-return standard deviations per square-root second. Probabilities entering logit are clipped to [0.000001, 0.999999]. The May fit retains 10 nonzero coefficients: the asset, confidence, volatility-ratio and open-interest terms are zero.

Training proceeds in five steps:

1. Compute market error **y − p**.
2. Compute variance weight **w = max(0.0001, p(1−p))**.
3. Divide the error by sqrt(w): target **z = (y−p)/sqrt(w)**.
4. Multiply features by sqrt(w): **X = sqrt(w) × x**.
5. Fit ARDRegression to predict z from X, with at most 300 iterations and no separate intercept. The constant feature supplies the intercept-like term.

This approximates one regularized Newton correction for binary log loss. It does not directly optimize the final clipped probabilities' log loss. Call the fitted coefficients **beta**.

Prediction proceeds in six steps:

1. Compute residual prediction **r = X × beta**.
2. Undo variance scaling: **r / sqrt(w)**.
3. Cap this correction to **[−1, +1]**.
4. Multiply by **0.75** for setting 03, or **0.50** for method 24.
5. Add the correction to market log odds.
6. Convert back with sigmoid, 1/(1+exp(−value)), and clip to [0.000001, 0.999999].

Negative corrections lower YES probability; positive corrections raise it. Strength 0.75 means 75% of the bounded log-odds correction, not multiplying every probability by 75%.

Reconstructed standardized-residual training MSE versus predicting zero correction is **0.887070 vs 0.896618** (February fit), **0.894940 vs 0.902371** (March), and **0.886588 vs 0.892478** (May). These are unpenalized, forecast-weighted residual errors, not ARD's full Bayesian objective or binary log loss. No original ARD iteration-loss trace was saved.

## Evidence and limits

All **137,727 saved out-of-sample forecasts per model** for method 24 and setting 03 were reconstructed from saved fits. Maximum probability discrepancy: **2.22 × 10⁻¹⁶**. No refitting, new pocket selection or protected later-outcome access occurred.

Market probabilities come from closed one-minute Kalshi bid/ask candles; exact quote-update timestamps are unavailable. Alongside prior validation exposure and statistical uncertainty, this limits the conclusion to a promising historical pocket. Keep the candidate fixed and seek an admissible, previously unexamined replication interval with better quote timing.

Sources and outputs:

- [Interactive comparison display](/mnt/c/Users/russe/.cursor/projects/empty-window/canvases/crypto-training-validation.canvas.tsx).
- [Exact results and original curve values](/home/russell/data/crypto_trading/research/training-explanation-20261018/results.json).
- [Comparison metrics CSV](/home/russell/data/crypto_trading/research/training-explanation-20261018/metrics.csv).
- [Warm-up pipeline](../../configs/run-pocket-tuning-warmup-03-p24-strength-075.json) and [validation pipeline](../../configs/run-pocket-tuning-validation-03-p24-strength-075.json).
- [Selection lock](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/selection-lock.json) and [selection program](/home/russell/data/crypto_trading/research/pocket-tuning-20261018/freeze_selection.py).
- [Attribution memo](../memos/2026-10-18-attribution.md), [pocket-tuning memo](../memos/2026-10-18-pocket-tuning.md), and [aligned-method memo](../memos/2026-10-18-warmup20-aligned-methods.md).
