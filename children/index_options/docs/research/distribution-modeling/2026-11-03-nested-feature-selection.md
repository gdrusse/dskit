## Question

Can controlled forward/backward feature selection determine a smaller prediction feature set and reduce overfitting relative to the preset ten-feature Lasso?

## Finding

The frozen primary nested rule selects 3, 3, 1 and 2 active features for forecast years 2020–2023. On the same 449,498 annual observations, exact primary twCRPS is 0.527049634: +0.412765% versus calibrated market (paired 95% expiry/symbol-clustered interval +0.078238% to +0.747292%). All four annual gains are positive: +0.060404%, +0.539194%, +0.859905%, +0.159426%.

The previous ten-feature Lasso remains slightly better overall: the new primary gain against it is −0.058646% (interval −0.236208% to +0.118915%). This does not establish superiority or equivalence. Backward selection gains +0.410169% against calibrated market; nested unrestricted Lasso, retaining 37/58/31/28 active inputs, gains +0.387487%. No post-evaluation model choice or retuning was performed.

The primary features are rv_22, chain_log_atm_iv and rn_right_tail_integral in 2020 and 2021; rn_right_tail_integral alone in 2022; rv_22 and rn_right_tail_integral in 2023. None of the delta/gamma/vega-weighted open-interest candidates survives in the primary model. Forward/backward active-feature Jaccard overlap is 1/1/1/0.5; consecutive primary overlap is 1/0.3333/0.5. The count is regime-dependent, not an established universal optimum.

Primary training gains versus its training-only constant are 1.982%, 2.252%, 1.321%, 1.620%; historical check gains are 2.429%, 0.070%, 1.036%, 1.442%. The 2021 collapse remains a generalization warning. Different time periods prevent treating that gap as a pure overfitting estimate. Primary full CRPS is 0.662323 using the same 401 midpoint-quantile approximation; pooled predicted-5% tails contain 4.45% and 4.97%. Per-year exact Brier components accompany the results.

## Method

The owner authorized this separate standalone prediction study. The frozen protocol searched all counts 0–106, seven fixed Lasso penalties, and the training-only intercept. Forward additions and backward removals rank training-only weighted least squares with ridge 1e−8 for numerical stability; each subset receives ordinary Lasso refits. This is a greedy training-ranked subset path, not exhaustive selection or validation-ranked lookahead.

For forecast year Y, the three inner validation quarters are Q4(Y−2), Q1(Y−1), Q2(Y−1). Training expands from 2017 with quotes and settlements strictly preceding validation. Imputation/scaling and paths are recalculated in each fold. Pooled row-weighted validation loss selects direction/count/penalty. The one-SE tolerance uses the best candidate's paired gain against its training-only constant, clustered by expiry and symbol. Among eligible candidates choose fewest maximum active inputs across folds, then smaller allowed subset, stronger penalty, and fixed method order. All 20 final models (including unranked 2019 warmup and procedure-specific controls) were hashed before historical checks and annual scoring.

Locked A0001/A0002/A0003/A0005/A0008 are preserved: standardized terminal distribution, exact primary score, chronological purges, clustered comparisons, annual refits. 2020–2023 were already exposed in prior work; the intervals are exploratory and not adjusted for repeated searches. The 106-input inventory retains unresolved source-clock/open-interest debt, although no OI-named input is active in the primary model. Protected 2024–2025 and 2026 stay sealed. No MIO, production implementation, or promotion.

## Sources

Canonical evidence: `/home/russell/data/index_options/mlp-learning-audit-20261103/nested-selection-study/`: `protocol.json`, `selection-results.json`, `results.json`, `verification.json`, `feature-stability.json`, `worker-parity.json`, `dispatch-results.json`, `models/`, per-year predictions and inner-path scores. Worktree base f6e4767492c2e52a4d1393b6ab82e462c6b80ead. All 15 inner jobs verified; WSL/Mac parity had identical paths and score differences below 1.3e−14. Annual primary-score reconstruction differs by at most 1.34e−15; original training input hashes and all row identities match. Focused duplicate/constant-feature, direct-Lasso, greedy-path, clustered-SE, one-SE and date-purge checks passed. No full test suite was run.

Report: `http://localhost:8765/nested-feature-selection.html`; interactive canvas: `/home/russell/.cursor/projects/home-russell-dskit/canvases/nested-feature-selection.canvas.tsx`.
