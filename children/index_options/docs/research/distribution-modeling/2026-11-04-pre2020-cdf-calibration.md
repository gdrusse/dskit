## Question and scope

Can the forecast distribution be made to agree with actual outcomes using only pre-2020 evidence? The user explicitly authorized pre-2020 development for this check. This is a bounded calibration follow-up to the distribution-shape audit, not an extension of the closed component search or a production promotion. A0001/A0002 targets and proper scores, A0003 date cutoffs, and A0008 treatment of 2019 warm-up remain in force. No owner Path edit.

## Procedure

Use saved original M53 fold-1 validation forecasts with fitted=true, market_eligible=true, and both quote and settlement before 2020. There are 77,581 unique matched forecasts, 92 stocks, beginning 6 March 2019. No earlier in-sample or later-fold fitting predictions augment this cohort. The 2016–2018 history supports earlier descriptive evidence but cannot manufacture missing out-of-sample M53 predictions.

The frozen budget contains unchanged M53, a global 21-knot empirical PIT correction, and a correction by the existing eight DTE bands with a minimum of 5,000 learning rows per band, otherwise global fallback. Reuse the existing _PitMap fit/apply implementation and _Grid exact13 score/PIT representation; no core capability was added.

Fit on 25,244 outcomes settled before July 2019; select strict minimum primary twCRPS on 19,133 July–September quotes settled before October. Persist the choice before loading Q4 outcomes. Refit on 51,460 outcomes settled before October and check 19,018 October–December quotes settled before 2020. These settlement purges exclude crossing-boundary outcomes from the corresponding check. No one-standard-error preference is used.

No DTE band reaches 5,000 learning rows in the first fit, so global and DTE predictions are identical in the selection check. The fixed tie order selects global. Six bands reach the threshold in the final refit; 1–7 and 50–60 DTE still fall back. Therefore the earlier selection did not validate distinct DTE maps.

## Results and decision

The global correction improves July–September primary twCRPS from 0.526448719689 to 0.512437568429, a 2.66145% descriptive gain; the pointwise two-way clustered 95% interval is −1.92686% to +7.24975%. Its mean absolute mean-CDF gap improves from 4.41877 to 2.15098 percentage points.

In October–December, unchanged M53 scores 0.484615033164 primary twCRPS and 0.612616912268 full CRPS. Global correction scores 0.507199064804 and 0.639762304558. Paired primary improvement is −4.66020%, interval [−6.74130%, −2.57910%]; full CRPS improvement is −4.43105%, interval [−6.42066%, −2.44145%]. There are only 13 expiry clusters, 88 stock clusters, and 971 intersections in Q4.

The Q4 pooled mean absolute mean-CDF gap increases from 5.86226 to 7.76201 percentage points; equal-DTE gap rises from 6.21340 to 8.09376 points. The DTE control also worsens primary loss (−4.49117%) and calibration; both corrections worsen the mean probability gap in all eight bands. Training-map diagnostics show much better calibration, which does not transfer to the later check.

The selected global recipe fails all four frozen performance guards: pooled calibration improvement, equal-DTE calibration improvement, no primary-loss worsening, and no full-CRPS worsening. Retain unchanged M53. No corrected model is adopted or promoted. This result does not establish that unchanged M53 is well calibrated.

The pooled 2019 mean absolute mean-CDF gap of 0.65236 percentage points hides opposing errors: M53 predicts too little probability below many thresholds in July–September, then too much in October–December. Time aggregation can conceal instability. The next evidentiary need is more genuinely earlier out-of-sample predictions and a predeclared time-stability assessment, not fitting an historical marginal shape onto every conditional forecast.

## Verification and limits

Independent correctness and tests/integration reviews, candidate hashes, saved maps, row-level scores and presentation checks accompany the study. The canvas is typechecked; the HTML counterpart is inspected in the app browser, including period/DTE/model controls and curve/error-bar rendering. No full test suite was run because no core or model code changed.

All outcomes returned by reads precede 2020. 2019 was already inspected in development, so this is chronological reuse rather than a pristine holdout. Matching average probabilities does not establish conditional calibration. There is no agreed practical equivalence tolerance, and intervals containing zero do not prove a distribution match. Two-way clustered intervals are pointwise and do not account for repeated model selection or all serial dependence from overlapping labels. Late-quarter settlement cutoffs thin long-DTE coverage. Source-clock and universe assumptions remain inherited and uncertified.

PIT and full CRPS use the common 13-knot CDF with linear closure to 0/1 at Z = −7/+7, not the native model density or an estimated tail law. Mean absolute mean-CDF gap is an aggregate calibration diagnostic, not per-row prediction error.

The first attempt completed scientific calculations but failed while hashing a misnamed Path document. The first selection lock/log were preserved. The repair changed the path and added an assertion of unchanged selection; both scientific outputs and exact first-source provenance were independently reproduced. No method, parameter, boundary, or selection criterion changed after Q4 scoring.

## Artifacts

/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/protocol.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/candidate-lock.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/results.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/selection-lock-attempt1.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/selection-lock.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/correctness-review.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/tests-review.json
/home/russell/data/index_options/mlp-learning-audit-20261103/pre2020-cdf-calibration/presentation-verification.json
/home/russell/.cursor/projects/home-russell-dskit/canvases/CDF-calibration-pre2020.canvas.tsx
http://localhost:8765/CDF-calibration-pre2020.html
