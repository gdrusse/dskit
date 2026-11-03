## Question

Can features predict the signed probability errors of the calibrated market and improve the entire forecast distribution on later dates?

## Finding

This bounded study was approved by Russell on 2026-11-03; execution is in progress. Direct standalone forecasts and M53 correction studies already exist. The distinct target here is the calibrated market's threshold-indicator residual, with a systematic, fixed feature-family comparison. ADR-0290 authorizes implementation and the retrospective run. No new annual result has been scored.

### Why this target

For quote-time information x, realized standardized terminal return y, and the already calibrated market CDF F0, define at each fixed threshold z:

```math
r_j = \mathbf{1}\{y\leq z_j\} - F_0(z_j),\qquad
\widehat F = \Pi\{F_0+\widehat{\mathbb E}[r\mid F_0,x]\}.
```

Positive residual means the market assigned too little probability below that threshold. Its conditional expectation, not a single noisy realized miss, is the learnable object. Projection Pi produces a valid CDF. Zero correction returns the original baseline exactly. Squared error on r before projection is the corresponding threshold probability loss on F0+r; selection and reporting always score the final projected CDF.

This differs from predicting price, absolute error, or the scalar CRPS loss. A magnitude prediction alone cannot tell us how to correct a probability. It also differs from the rejected M53 median-location heads and from the separate frozen-model blend diagnostic currently underway in another chat. Residual fitting is not automatically superior to direct modeling: the advantage to test is the fixed market anchor and restricted correction.

### Frozen scope and controls

- Base code: f6e4767492c2e52a4d1393b6ab82e462c6b80ead, isolated WSL checkout `/home/russell/wt/market-error-study-20261103`.
- Bind A0001/A0002/A0003/A0005/A0006/A0007/A0008; A0008 supersedes the old A0004 screen rung. No Path edits. Research prediction only.
- Anchor: the `market_cal` component, not final M53. Reconstruct each saved annual CDF from raw market probabilities and that fold's saved 21-knot PIT map. Component fit description SHA256: f9ddf3ff2f45a27f0fd9c37065828ec4a8e471e65b0d2317108cfb433167e04d (verified from bytes).
- Referenced raw scored-rows SHA256: f0db364e4d149fe7dc22a34e2de90ef117599d32a2ab6705fe8da8e03f7b2c85. Referenced feature panel SHA256: 186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144. These large-file pins come from existing receipts and must be verified before use; not reverified here.
- Primary cohort: the historical audit's common 449,498 eligible annual identities in 2020-2023, key `(symbol, quote_date, expiry)`, identity hash d9b0ccd654b66e537b8c1fd8148bcf70640a2f1b587abecf7400fedc4ef2bd05. Verify uniqueness, exact identity and labels against source before scoring. Do not substitute the prior M53 correction study's 527,079-row cohort. Missing X is imputed, not dropped; duplicate/missing baseline or ambiguous join refuses.
- Controls on exactly these rows: unchanged calibrated market (exact null); a residual head using only F0 and an intercept (further calibration); raw market; per-name horizon empirical; pooled empirical. Saved M53 and standalone predictions are contextual comparisons only, with no blend fitting here.
- A correction must beat the calibration-only control to support a claim of incremental feature information. Feature importance alone is insufficient.

### Feature register and bounded search

Stage one uses the exact 38 retained predictors in the prior timing-restricted study, frozen in `protocol.json`: 24 return/momentum, 8 realized/directional volatility, 4 stock-liquidity, 2 maturity/calendar. All are candidates for retrospective analysis, not certified decision-time inputs.

For every outer fit, compare F0-only, each of the 38 individual features plus F0, and each of the 15 nonempty combinations of the four complete families plus F0. The same 13 baseline probabilities and one intercept are present in every non-null head. There are 54 feature specifications. No forward search, interactions, PCA, per-ticker choice, or post-score feature addition.

Use one multi-output linear ridge family. Per threshold, minimize mean squared residual/2 plus alpha times the squared slope norm/2; intercept is unpenalized, alpha normalized by training-row count. Train-only mean imputation and standardization; all-missing or zero-variance columns become zero with a flag. Fixed alpha grid: 100,10,1,0.1,0.01,0.001,0.0001. Thus 378 non-null settings plus the explicit exact-null candidate per outer year. No one-standard-error rule; choose the strict minimum pooled row-weighted inner-validation twCRPS. Exact ties use null first, then F0-only, individuals in frozen feature order, family bitmasks 1..15, penalties in the stated order. No grid expansion on boundary optima.

Projection: weighted isotonic regression over the 13 ordered thresholds with the positive A0002 trapezoid coefficients, constrained to [0,1]. Reuse sklearn's existing isotonic implementation. The explicit null bypasses all preprocessing/projection and returns F0 bytes unchanged. Log the weighted RMS change and fraction of repaired rows so the correction's size and projection effect are visible.

Quarantined next-stage hypotheses: option-implied shape/term/variance-premium; quote quality, spread and flow; known event/calendar data; trailing settled forecast-error summaries. These are scientifically plausible hypotheses, not established signals. The 68 option-derived predictors have unresolved vendor-underlying/IV/OI timing and indirect eligibility dependencies. Do not restore them merely by lagging a column. New families or error-history features need a separately frozen amendment and availability evidence before outcome scoring.

### Chronology and exposure

1. Preflight the anchor and its raw inputs. For every saved baseline map, record the actual calibration row identities and maximum outcome-availability timestamp. The availability date must precede the earliest forecast it teaches. Amendment approved by Russell on 2026-11-03 ("Proceed with the stated assumption"): historical publication timestamps are missing, so use settlement dates as an explicit availability assumption. Retain the actual 2026 backfill acquisition timestamps; these do not certify historical availability. Any unreconstructable or noncausal map stops fitting with a documented reason; do not silently replace the baseline.
2. Build a chronological forecast ledger of historically issued F0 values. Residual training labels use that row's own out-of-time baseline map, never a later outer-year map reapplied to its training rows. Recover the same 21-knot recipe on expanding, already settled history for missing 2018 blocks, using only 2017-and-earlier-2018 eligible history; save exact cutoffs, row IDs and maps. If the original recipe or sufficient history is unavailable, stop and amend before fitting. No random cross-validation.
3. Preserve the 2019 warmup as unranked timing/learning diagnostics. It uses only eligible 2017-2018 history, a fixed alpha100 full-family head and exact null; do not tune or select from that outer warmup score. A0003 still permits settled2019 development history inside the2020 training/inner-selection boundary; the original2019 warmup-model score itself never ranks a candidate.
4. For each outer forecast year Y=2020,2021,2022,2023, use only quotes before January1 Y and labels known/settled strictly before it. Inner validation quarters are Q4 of Y-2, Q1 of Y-1, Q2 of Y-1, with expanding earlier training. Inner validation labels must settle within their quarter and the outer training boundary. Recreate residual targets, imputation and scaling using only legal earlier forecasts/history. Remove overlapping identities and all labels reaching a boundary. The existing original monitoring band is check-only, excluded from model fitting and selection.
5. Refit the selected setting on all eligible outer training residuals (excluding the original monitoring band). Freeze all four yearly selections and every compared fitted candidate before reading new annual scores. A prediction-only pass commits identities and CDF hashes without materializing outcome columns; subsequent chunked regeneration must match those hashes. Retain selected CDFs and paired row-loss arrays rather than every candidate probability matrix. The outer baseline stays the original saved annual market_cal; historical residual targets stay their original issue-time baselines.
6. The outer years have been examined repeatedly. Treat results as retrospective development and report the full search count. No fresh-confirmation language, no promotion or holdout opening. 2024-2025 remain sealed; 2026 remains sealed. Filter quote dates AND outcome availability before materializing new study data; never access protected outcomes to complete a label.

### Scores, feature evidence and stop rule

Primary: exact A0002 13-node trapezoidal twCRPS, z=-3,-2.5,...,3, coefficients 0.0625 at endpoints, 0.125 at zero, 0.5 elsewhere. Lower is better. Score full matched rows and each year; decompose existing regimes, ticker and expiry-rank cells without selecting the evaluation universe by outcomes. Strike regions remain pooled decompositions, not cells.

Alongside: full-distribution CRPS for the explicitly declared piecewise-linear CDF with unchanged source tail width4 (zero at z=-7, one at z=7), grid-unweighted CRPS separately labeled, threshold Brier/reliability, PIT/tail summaries and Berkowitz diagnostic. Reuse public GridCurve methods for full-distribution scoring. This finite-tail reconstruction is the same for anchor and corrections and must not be presented as the unknown true market tails. Null equality applies to the reconstructed full distribution as well as its 13 probabilities.

Report paired mean loss differences and two-way expiry/symbol cluster-robust 95% intervals using the audited paired-mean formula in the prior `mlp-learning-audit-20261103/audit_uncertainty.py:measure` (expiry + symbol - intersection, G/(G-1), Student-t minG-1); record cluster counts and any nonpositive variance failure. Do not import that script, whose top level scores real data; reuse its verified arithmetic through the existing research evaluation owner. Refuse fewer than two clusters in either dimension. Pointwise intervals do not repair prior adaptive searches. A moving calendar-block sensitivity can be proposed later; do not add it after seeing results.

For every family, compare frozen family-only and all-family-minus-family procedures with F0-only and all-family procedures respectively. Each procedure selects its penalty in inner history only, then produces its own frozen outer predictions; report every pair, including negative results. Individual-feature ranks are descriptive, conditional on this input set, not causal discoveries. Selection frequency and coefficient stability are secondary diagnostics.

Predeclared study continuation bar: primary selected procedure improves over unchanged market_cal AND F0-only in pooled2020-2023 and separately in2021,2022,2023; report2020 explicitly, including any loss. Require the pointwise pooled paired95% interval to favor improvement against both controls before proposing a further study. This is a conservative research continuation gate, not a replacement for A0005/A0007 or permission to promote. Promotion remains unassessed and requires all existing baseline/cell/seed criteria and separate authority. If the bar fails, stop this feature/operator search with a null conclusion; no after-the-fact subset, penalty or model-family rescue.

### Data limits and operational gates

The current option inputs, inherited market anchor, historical cohort and corporate-action conventions have unresolved point-in-time provenance. Withholding option predictors does not fix the anchor or outcome-conditioned cohort. Stage one can only be a labeled retrospective mechanics/signal study, conditional on those assumptions. A promising result must be repeated on a separately approved, availability-certified reconstruction before any practical use.

Compute after approval: apply mac-compute-worker, inspect actual processes/claims, Windows physical RAM/C storage and WSL headroom (never sum their RAM). Budget one WSL pilot first, two numerical threads, <=1GiB RSS and <=250MiB new output; estimate complete job time before increasing concurrency. Preserve other sessions' runs. Mac only if a compatible WSL-backed streaming job improves completion time. No heavy job is started by this proposal.

Acceptance checks before real fitting: exact-null bitwise identity; hand-calculated indicator residual sign/loss; no future map/label access; duplicate and missing keys refuse; training-only preprocessing; all-missing inputs; bounded monotone CDFs; fullCRPS parity on reconstructed controls; inner minimum and tie order; complete candidate counts; source/code/config hashes; immutable prediction freeze before scoring; corrupt/missing cache refusal. Focused tests only.

### Existing seams and proposed change surface

Inventory read: pipeline README What ships; predictive_cdf.GridCurve, DynamicPITRecalibratedCDF and ChronologicalCDFStudy; cdf_grid_methods.GridCDFMethod; sklearn.StandardizedEstimator, SklearnExpandingRefit and TwoStageResidualEstimator. GridCDFMethod already owns bounded reads, source identities, folds, the exact grid score, PIT, fit artifacts and feature joins. Its binary_gbm relearns indicator probabilities with baseline logit as an input; it is not a fixed-offset error model. TwoStageResidualEstimator explicitly uses in-sample first-stage residuals and therefore must not be used as this study's causal baseline ledger.

Proposed implementation manifest after ADR approval: extend existing `dskit/pipeline/libs/cdf_grid_methods.py` with one JSON-configured residual-ridge method; use existing `dskit/pipeline/libs/cdf_research.py` workflow seams for the bounded candidate study, extending that owner only if required; focused tests in the matching existing test modules; one child config `children/index_options/configs/run-calibrated-market-error-study.json` referencing verified/onboarded source profiles. Update existing package/child layout docs if their documented surface changes. No new child fit loop, registry, estimator package, broker or production change. Any needed capability outside this manifest returns for a design amendment. Integration checks: the existing feature loader zeros an entire row when one feature is missing, so it cannot stand in for specified columnwise training-mean imputation; current publication scores before persisting forecasts, so it cannot stand in for the required pre-score freeze; current paired tables use the maximum of two one-way errors, so they cannot stand in for required two-way expiry/symbol intervals. These defaults must be bypassed or extended within the declared owners, with focused tests.

Run-owned evidence under `/home/russell/data/index_options/market-error-study-20261103`: protocol and input/code pins, issue-time calibration ledger, candidate/selection receipts, frozen fits/predictions, verification and comparison report. Read earlier studies only; never edit their worktree or artifacts. Baseline reconstruction is allowed only in this run's outputs, never overwriting source forecasts.

Sweep: `tools/sweep/sweep calibrated-market-error residual offset conditional-calibration` searched60refs/126worktrees and failed at the known50ms git-grep timeout; targeted source inventory above completed. Do not claim an exhaustive successful sweep.

## Sources

- Local locked Path; RE-ENTRY; `2026-11-03-m53-ten-method-causal-search.md` (rejected median/scale/PCA corrections, different baseline/cohort).
- Prior `mlp-learning-audit-20261103/ridge-stability-study/protocol.json` and `timing-audit.json`: exact38 inputs,68 excluded option inputs, inherited cohort limitations. Other chat's ongoing frozen-model blend is separate.
- [Song et al., Distribution calibration for regression, ICML2019](https://proceedings.mlr.press/v97/song19a.html): post-hoc distribution correction is a recognized method; this paper does not establish an options-market edge or validate our proposed ridge model.
- [Dheur and Ben Taieb, ICML2023 calibration study](https://proceedings.mlr.press/v202/dheur23a.html): calibration and sharpness can trade off; calibration plots alone are not an accuracy verdict. Application here is our proposed inference.
