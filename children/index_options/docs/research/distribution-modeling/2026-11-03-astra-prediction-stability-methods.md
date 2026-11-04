## Question

Which methods could address unstable Lasso feature selection: segmentation, PCA, changing the stock universe, or other approaches?

## Finding

Ten remains a historical benchmark, not a proven optimum. The most useful next step is to separate changing feature sets from changing forecasts, clear source-time concerns, then test one ridge challenger under exactly the existing validation procedure. Segmentation, PCA and stock-universe changes could help under specific conditions, but introducing them together would make the result hard to interpret.

Research completed by an Astra subagent using the supplied verified results and primary literature. The parent added descriptive distances from saved validation curves. No new model was trained. The separate chat remains stalled at its original approval request; this investigation completed here without changing approval settings or executing that request.

Selection noise is a leading explanation: The latest selector searches 5,195 settings across three validation quarters. Correlated candidates are not 5,195 independent trials, but even correct chronological validation can overfit a noisy selection criterion. The existing evidence does not establish a stable optimum.

Count changes are not forecast changes: Counts vary substantially when one quarter is omitted, while conditional inner feature-set overlap is about 100%, 75.5%, 73.3% and 100%. That overlap conditions on the selected count and penalty; it does not measure stability of the whole procedure. Compare CDFs and tail probabilities on common rows before deciding instability is economically or predictively material.

Some minima are nearly flat: For 2022, one feature has validation loss only 0.00264% above the four-feature winner; 46 features are 0.03982% above it. For 2021, 63 features are 0.06004% above the 19-feature winner. These are descriptive loss distances, not uncertainty bounds or evidence of equivalent forecasts. Keep strict-minimum selection; do not silently restore one-SE.

Validation changes matter independently of count: Variable count gains 0.305% over calibrated market, rolling fixed ten gains 0.204%, and original ten gains 0.471%. Rolling fixed ten also trails original ten, so count alone cannot explain deterioration. Period coverage, recency and row weights need inspection. All comparisons remain exploratory.

Dependence reduces information: 449,498 rows include overlapping labels, common market shocks and repeated symbols. About 209 expiry clusters and 110 symbols describe dependence; neither is an effective independent sample size. Preserve paired forecasts and examine contiguous temporal blocks containing the cross-section. Expiry clustering alone need not capture dependence across overlapping expiries.

Overfitting and drift remain unresolved: The 2021 model gains 3.318% over constant in training but loses 0.658% on the later historical check. This can reflect fitted noise, changed conditions or both. Four annual outcomes do not identify a reliable regime rule, and repeatedly examined outcomes cannot supply fresh confirmation.

The investigation ranks methods as follows. This is research judgment, not a measured ranking of their performance on this dataset.

- Feature timing and forecast diagnostics (First): Check whether inputs were available at prediction time; distinguish unstable counts from unstable forecasts. Limitation: Unresolved open-interest clocks can invalidate an apparent gain. Stable feature counts alone say little about forecast quality. Compute: Low; saved outputs first.
- Ridge with the same eligible features (First model experiment): Continuous shrinkage avoids hard subset boundaries among correlated inputs and reduces the search to one penalty. Limitation: May retain irrelevant directions; cannot manufacture missing signal or solve changing relationships. Compute: Low to moderate.
- Fixed shrinkage toward calibrated market (Next cheap candidate): Reduces exposure to a weak incremental forecast; a convex average of valid CDFs is valid. Limitation: Shared bias persists. Calibration must obey historical boundaries; tuning many weights or segments adds selection risk. Compute: Very low if forecasts are saved.
- Elastic net; temporal-block averaging (If correlated substitutes or forecasts are unstable): Elastic net can retain correlated inputs together; averaging can reduce variability of unstable predictors. Limitation: Mixing fractions and resampling rules add choices. Blocks must preserve relevant dependence; averaging can preserve bias. Compute: Moderate.
- PCA, family compression or PLS (Conditional): Compresses redundant families; PLS uses target information when high-variance directions miss predictive signal. Limitation: PCA preserves predictor variance, not incremental predictability. Components can drift; supervised PLS adds estimation risk. Compute: Moderate.
- Pooled interactions and partial pooling (Conditional, before hard segmentation): Sector, liquidity, coverage, volatility or horizon may have repeatable differences; partial pooling shrinks weak groups toward a shared model. Limitation: Many rows within a group do not create many distinct market episodes. Wrong pooling assumptions obscure differences. Compute: Moderate to high.
- Change the stock universe (Only for a data-quality or deployment reason): Point-in-time liquidity/coverage eligibility may improve measurement; new names can broaden cross-sectional coverage. Limitation: Changes the population and baseline difficulty; does not create new market regimes. Avoid survivors and outcome-selected winners. Compute: Moderate to high.
- Rolling windows or recency weighting (Only after drift diagnostics): Can trade stale-history bias for greater responsiveness. Limitation: Less effective history increases estimation variance; do not jointly tune windows, weights, counts and segments. Compute: Moderate to high.
- Hard segmentation, group DRO or broad new searches (Low current priority): May suit clear group mechanisms or an explicitly different worst-group objective. Limitation: Splits scarce time information, adds many decisions, and may change the objective. No guarantee against future regime shifts. Compute: High.

Recommended bounded experiment, not yet executed:

1. Hypothesis: continuous shrinkage over eligible inputs may forecast better than selecting ranking penalty, feature count and refit penalty.
2. After source-time review, freeze the eligible input version, historical folds, expanding training, settlement purge, full-training refit, identities, row weights and forecast representation.
3. Compare one ridge family with a predeclared short path of seven L2 strengths plus the null control. Record numerical values and objective normalization before fitting; Lasso penalty values cannot simply be copied as equivalent ridge strengths.
4. Use the same strict minimum pooled 13-node projected-CDF twCRPS. Retain the current variable-count selector as the primary matched-procedure comparator; rolling fixed ten, original ten and calibrated market remain contextual controls.
5. Keep PCA, segmentation, universe changes, time weighting and blending out of this particular experiment so its interpretation is clear. No one-SE preference.
6. Report paired primary loss differences, forecast distances, annual variation, full CRPS and calibration. Constant nominal feature count is not a success criterion.
7. Stop after the registered path and report. A grid-edge optimum is a documented limitation, not automatic permission to expand the search. No further sweep has been launched.
8. A favorable development result justifies freezing a candidate, not declaring generalization. Predeclare a future comparison on genuinely unused settled outcomes; protected 2024–2025 and 2026 remain sealed.

How to test segmentation fairly: Begin with error decomposition relative to calibrated market. Use historical sector membership, available liquidity/coverage, lagged or currently observable volatility, and known horizon. Prefer a few pooled interactions before independent learners. Avoid crossing many group axes. High absolute loss in a segment can reflect a harder population rather than a missing specialized model.

How to test PCA fairly: Fit imputation, scaling and loadings only in each training fold. Select component count within the temporal procedure. Compare represented subspaces and forecasts rather than component signs. Family-level compression can preserve distinctions such as left and right tails. PCA may discard low-variance predictive contrasts; PLS is supervised and needs its own controlled selection.

How to test a stock-set change fairly: Define eligibility from information available at each forecast date. Report both common-row performance and performance on the proposed full universe, with calibrated market scored on exactly those rows. Explain exclusions and composition effects. Leave-symbol/sector-out tests address transfer to new names; they complement rather than automatically replace temporal validation for existing stocks.

How to test market anchoring fairly: A fixed convex mixture of valid market and model CDFs is valid. Start with one prespecified weight if pursued later. A learned correction is a separate distributional model, not ordinary scalar-return residual regression; historical calibration and probability validity must be respected. Do not use market calibration fitted with unavailable future labels.

Recommendations are informed hypotheses, not measured improvements. All 2020–2023 outcomes have been repeatedly inspected; pointwise intervals do not adjust for repeated searches. There may be too little stable incremental information beyond calibrated market. Protected outcomes remain sealed; no MIO or production promotion.

No new model training, production changes, full test-suite runs, or protected-data access occurred. Source hashes remained unchanged. The research canvas passed a TypeScript check and the browser report was visually inspected.

## Sources

- Supplied verified study: /home/russell/data/index_options/mlp-learning-audit-20261103/lasso-rolling-topk-study; original sources are hash-pinned in /home/russell/data/index_options/mlp-learning-audit-20261103/astra-stability-research/evidence.json.
- Astra research subagent: /root/astra_stability_research. Public-source investigation only; local project claims were supplied by the parent.
- [Cawley & Talbot (2010): overfitting in model selection](https://www.jmlr.org/papers/v11/cawley10a.html).
- [Zou & Hastie (2005): elastic net](https://doi.org/10.1111/j.1467-9868.2005.00503.x).
- [scikit-learn: PCR versus PLS](https://scikit-learn.org/stable/auto_examples/cross_decomposition/plot_pcr_vs_pls.html).
- [scikit-learn: data leakage and preprocessing](https://scikit-learn.org/stable/common_pitfalls.html).
- [Gelman (2006): multilevel models](https://sites.stat.columbia.edu/gelman/research/unpublished/multi.pdf).
- [Meinshausen & Bühlmann (2010): stability selection](https://arxiv.org/abs/0809.2932).
- [Breiman (1996): bagging predictors](https://doi.org/10.1007/BF00058655).
- [Gneiting & Raftery (2007): proper scoring rules](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf).
- [Shumway (1997): delisting bias](https://doi.org/10.1111/j.1540-6261.1997.tb03818.x).
- [Cameron, Gelbach & Miller: multiway clustering](https://cameron.econ.ucdavis.edu/research/multiway06.html).
- [Pesaran, Pick & Pranovich: forecasts with structural breaks](https://www.dnb.nl/en/publications/research-publications/working-paper-2011/327-optimal-forecasts-in-the-presence-of-structural-breaks/).
- [Sagawa et al. (2020): group distributional robustness](https://arxiv.org/abs/1911.08731).
- [Yuan & Lin (2006): grouped variable selection](https://sites.stat.washington.edu/courses/stat527/s13/readings/yuanlin07.pdf).
- /home/russell/data/index_options/mlp-learning-audit-20261103/astra-stability-research/research-report.json.
- /home/russell/.cursor/projects/home-russell-dskit/canvases/astra-prediction-stability.canvas.tsx.
- Browser report: http://localhost:8765/astra-stability-research.html.
