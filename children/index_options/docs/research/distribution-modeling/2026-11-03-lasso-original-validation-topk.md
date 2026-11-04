## Question

Can the original ten-feature Lasso procedure choose its feature count naturally, using exactly the same feature ranking and historical validation procedure?

## Finding

The authorized extension tested the top 1 through top 106 features from each saved Lasso106 coefficient ranking, using the original training-only preprocessing, historical monitor bands, seven penalties, null forecast and exact projected-CDF twCRPS. No one-standard-error rule. The original monitor selects source-model penalty/ranking, subset count and final penalty. Counts are selected separately each year; this is an expansion of the original top-ten workflow, not greedy forward/backward selection or three-quarter nested validation.

All five ten-feature controls reproduced the original coefficients, predictions and seven monitor scores exactly. Every count/penalty combination was fitted and all five chosen models were frozen before annual scoring. On the same 449,498 observations in 2020–2023, the selected counts are 11, 9, 35 and 12, with penalties 0.003, 0.01, 0.003 and 0.0001. All selected inputs are active under the original weighted coefficient norm. The 2019 warmup selected five features and is excluded from ranking.

The chosen-count method scores 0.527397315450 primary twCRPS, gaining +0.347070% against calibrated market (95% expiry/symbol interval +0.002415% to +0.691725%). The ten-feature control scores 0.526740719780 and gains +0.471135%. Direct chosen-count gain versus ten features is -0.124653% (-0.259423% to +0.010118%): worse on the point estimate, without a conclusive paired difference. Full CRPS is 0.663035 versus 0.662117 for ten features. The new model's 5th/95th percentile tail exceedances are 4.602% and 4.858%.

Annual chosen-count gains versus calibrated market are +0.623497%, +0.708453%, -0.189730% and +0.264392%; ten-feature gains are +0.567292%, +0.728471%, +0.333309% and +0.255432%. Most deterioration comes from 2022's 35-feature choice. Its training gain over the constant increases from 2.407283% to 2.704611% and monitor gain increases from 1.204809% to 1.524939%, yet following-year prediction deteriorates. This warns that feature-count selection may fit period-specific noise; regime shifts may also contribute. The monitor is used for tuning and is not an independent generalization check.

Each selected validation score is no higher than the exact ten-feature candidate, as guaranteed by inclusion. There is no such guarantee for later predictions. Three annual minima fall near ten, while one grows substantially. No additional count restriction or favorable year exclusion was added after seeing results.

All 3,715 candidate combinations including warmup were verified against the absolute-minimum rule. Original KKT residuals are at most 3.65e-8. Source/model hashes, label settlement ordering, finite monotone probabilities and identities pass; annual float32 CDF rescoring agrees within 1.52e-7 and every reference/control score agrees exactly. Original outputs remain unchanged. Two WSL workers with two numeric threads completed the five independent fits in approximately 59 seconds including startup. A summary serialization issue caused by absent missing-rate metadata was repaired using unchanged saved annual forecasts; no refit or reselection occurred. No full test suite ran.

Original annual results were already exposed before this amendment. Results are exploratory, with pointwise intervals and no repeated-search correction. Historical source-time caveats remain. Protected 2024–2025 and 2026 stayed sealed. No MIO, allocation, production promotion or shared-registry changes.

## Sources

- User request to preserve the original ten-feature Lasso validation while selecting feature count; subsequent status request.
- Original source/control: /home/russell/data/index_options/mlp-learning-audit-20261103/lasso-study/protocol.json, run_lasso.py, fit-results.json and models/.
- New frozen protocol: /home/russell/data/index_options/mlp-learning-audit-20261103/lasso-topk-study/protocol.json; SHA256 b14eac9a89a602071aecc38c9d46cb89349e81d49c4fe991ad5e34f8e8aebd94.
- New results.json, selection-curves.json, training-validation.json, verification.json and jobs/fold*/result.json under that study directory.
- /home/russell/.cursor/projects/home-russell-dskit/canvases/lasso-topk-validation.canvas.tsx.
- Browser report: http://localhost:8765/lasso-topk.html.
