## Question

Use the tested patterns to finalize a historically stable, market-beating prediction recipe.

## Finding

Selected research recipe: m53. Selection uses the strict minimum primary score among original M53 and four frozen challengers meeting the stated historical baseline/stability screens. No one-SE rule. No production change or protected-window opening.

- m53: twCRPS 0.525580619400; gain vs calibrated market +0.690339%, pointwise95% interval [0.40378757499405404, 0.9768895289590498]; annual gains [0.930678062117468, 1.102912239249398, 0.42096760642110564, 0.31093614947787696]; weakest year +0.310936%. Full extended-line CRPS 0.661248864230.
- qa5_disp: twCRPS 0.525582848948; gain vs calibrated market +0.689917%, pointwise95% interval [0.39483421117786555, 0.9850003363068973]; annual gains [0.8732252509923139, 1.0905228420374389, 0.45913176266514677, 0.33825562422685923]; weakest year +0.338256%. Full extended-line CRPS 0.661259407185.
- cdf5_disp: twCRPS 0.525786341505; gain vs calibrated market +0.651467%, pointwise95% interval [0.36136414825346236, 0.9415696300347476]; annual gains [0.7635213546073082, 1.0864645191931046, 0.3872992296369216, 0.3686590698396668]; weakest year +0.368659%. Full extended-line CRPS 0.661106798567.
- cdf5_shrink: twCRPS 0.525748651263; gain vs calibrated market +0.658589%, pointwise95% interval [0.370399719286267, 0.9467773739697906]; annual gains [0.7449042333755641, 1.1165257002220617, 0.39456695730041474, 0.3770580042441205]; weakest year +0.377058%. Full extended-line CRPS 0.661059533968.
- cdf5_global: twCRPS 0.526042609731; gain vs calibrated market +0.603044%, pointwise95% interval [0.3232814858874643, 0.8828073563271411]; annual gains [0.5763535904484517, 1.103332647712048, 0.352288166614045, 0.37421133892872865]; weakest year +0.352288%. Full extended-line CRPS 0.661274021877.

Secondary descriptive alternative: cdf5_shrink is frozen separately in stability-alternative.json. Its mean fixed-input CDF movement is 11.300% smaller than M53; weakest annual calibrated-market point improvement is +0.377%, versus M53 +0.311%. Raw weight variation is higher than M53. This designation is post-result interpretation, not a changed selection rule: M53 remains the prespecified winner. The alternative has +0.659% pooled calibrated-market skill, 95% interval [+0.370%,+0.947%], but 2022/2023 annual intervals and its pooled raw-market interval cross zero. Its primary loss is 0.032% worse than M53, with a paired interval spanning no difference; full-distribution loss is 0.029% better, also uncertain. No future stability or new superiority claim.
Equal-weight averages over 72 blocks: shrunk-pool historical fitting skill 0.766%, next-block skill 0.757%; 49/72 blocks improve. This is not a new test of overfitting in the frozen base learners.

The three CDF recipes all use frozen MLP, original annual Lasso10, calibrated market, pooled empirical and per-name horizon empirical CDFs. The quantile recipe retains M53’s199levels, dispersion groups and100000-row deterministic cap percell. CDF recipes use all settled learning rows, so this is a recipe comparison, not an isolated operator effect. All follow21-calendar-day refits and settlement strictly before blockstart. Market maps are unchanged. Warmup-only strict-minimum shrinkage selection: {"scores": {"warmup_shrink_5000": 0.49890383305204467, "warmup_shrink_20000": 0.49888840619798025, "warmup_shrink_100000": 0.4989782824379933}, "selected_shrink_rows": 20000, "common_fitted_rows": 88643, "blocks": 17, "min_quote": "2019-01-23", "max_quote": "2019-12-26", "max_settlement": "2019-12-27", "frozen_at": "2026-11-03T22:48:19Z", "rule": "strict minimum row-weighted primary score; exact tie smaller strength", "protocol_sha256": "4f93cf4199891f858c2598967b6619bcbaff5d3432102eceb9b714752457dc49"}.

Stability diagnostics: {"m53": {"mean_block_l1_weight_change": 0.0463529597173325, "mean_block_fixed_input_cdf_rms_change": 0.0011563016533734907, "p95_block_fixed_input_cdf_rms_change": 0.003838780022366687, "forecast_probe": "Deterministic up-to256 current-block rows; prior/current weight laws on same component forecasts. Annual reset excluded."}, "qa5_disp": {"mean_block_l1_weight_change": 0.060722149664005534, "mean_block_fixed_input_cdf_rms_change": 0.0011848624045984403, "p95_block_fixed_input_cdf_rms_change": 0.004184388793820625, "forecast_probe": "Deterministic up-to256 current-block rows; prior/current weight laws on same component forecasts. Annual reset excluded."}, "cdf5_disp": {"mean_block_l1_weight_change": 0.05780967857324949, "mean_block_fixed_input_cdf_rms_change": 0.0011532990425629824, "p95_block_fixed_input_cdf_rms_change": 0.0038569705971115086, "forecast_probe": "Deterministic up-to256 current-block rows; prior/current weight laws on same component forecasts. Annual reset excluded."}, "cdf5_shrink": {"mean_block_l1_weight_change": 0.05454489010888822, "mean_block_fixed_input_cdf_rms_change": 0.0010256354163240436, "p95_block_fixed_input_cdf_rms_change": 0.003301991130269053, "forecast_probe": "Deterministic up-to256 current-block rows; prior/current weight laws on same component forecasts. Annual reset excluded."}, "cdf5_global": {"mean_block_l1_weight_change": 0.05060046663760875, "mean_block_fixed_input_cdf_rms_change": 0.0009033128030191781, "p95_block_fixed_input_cdf_rms_change": 0.002617014235484159, "forecast_probe": "Deterministic up-to256 current-block rows; prior/current weight laws on same component forecasts. Annual reset excluded."}}. Weight changes are not synonymous with forecast changes. Fixed-input probes apply consecutive weight laws to identical current-block component forecasts. Annual reset excluded. Training objectives and market-relative next-block validation scores are retained byblock; different time distributions prevent a pure overfitting interpretation.

Exact integral across whole real line of the common13-knot CDF with existing pit_tail linear extension to0/1. Includes outcomes beyond support; differs from original native25-node model CRPS.

Repeatedly inspected validation is retrospective, not fresh confirmation; pointwise intervals unadjusted for repeated selection.
CDF recipes use all settled learning rows; quantile recipe uses existing100000 cap percell. Recipe comparison does not isolate operator effect.
Frozen saved MLP seed and Lasso annual fits are reused; no new seed robustness claim.
Historical4-year sign screen does not guarantee future stability.
Inherited feature publication times, vendor underlying/IV/OI dependencies, adjustment vintages and future-path-conditioned cohort construction remain uncertified. Chronological combination fitting does not repair those source assumptions; this is not a causally certified or deployable model.

Prior patterns motivating this bounded search: wide feature selection and direct M53 corrections lacked reliable annual gains; ridge reduced sensitivity but lost accuracy; Bayesian shrinkage improved a weaker Lasso direction but did not beat original Lasso10; swappingLasso forMLP weakened M53 slightly, supporting investigation of complementary forecasts rather than standalone ranking alone. These studies used differing interventions/cohorts and are context, not a pooled leaderboard.

## Verification

Frozen cohort449498rows, exact13score parity, bothdatecutoffs, fullidentityjoins, validCDFs and allrankedpredictionsfitted. Existing horizonbaseline semantics verified. Independent correctness and tests reviews retained with hashes. Focused9 existing tests passed; syntheticfullCRPS integration matched independentquadrature. No fullsuite. ComputeWSL2only; independent2thread workers, no sharedregistry or source edits. Resourceprobes recorded.

## Sources

/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/protocol.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/candidate-lock.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/selected-model.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/stability-alternative.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/comparison.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/analysis-verification.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/correctness-review.json
/home/russell/data/index_options/mlp-learning-audit-20261103/m53-stable-combination/tests-review.json
/home/russell/.cursor/projects/home-russell-dskit/canvases/M53-stable-combination.canvas.tsx
