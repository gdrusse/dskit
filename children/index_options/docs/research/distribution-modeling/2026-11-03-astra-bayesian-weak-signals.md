## Question

User requested another Astra research subagent to investigate methods for weak and unstable predictive signals, specifically whether Bayesian models should be tried.

## Finding

Astra research completed. Bayesian inference can encode skepticism and share information, but there is no reviewed evidence establishing a win over this calibrated-market baseline on the locked twCRPS target. No new model was trained and no proposed diagnostic executed.

The latest same38-input experiment found ridge less sensitive but less accurate than Lasso, with both trailing calibrated market in every annual period. Timing uncertainties extend beyond OI; removing68 option-derived features does not prove their contribution was leakage. Retained inputs, inherited cohort and market baseline still lack point-in-time certification. The separate ten-method M53 search has different target/cohort and rejected tested corrections; it is context, not a numerically matched comparison.

Fixed Gaussian-prior linear posterior means are ridge-like. Regularized horseshoe offers a different sparse-signal assumption, but prior sparsity and slab choices matter. Partial pooling can help related noisy groups, but symbols do not supply independent market episodes. Dynamic models add an adaptation-speed assumption that four repeatedly exposed annual periods cannot reliably justify. Bayesian uncertainty is not measured predictive skill.

Closest literature: Bayesian beta calibration jointly models option-density dependence across maturities/dates; Bayesian nonparametric calibration combines daily S&P GARCH forecasts. These show relevant mechanisms in different baselines/scores, not an incremental twCRPS edge here. Empirical Bayes and combinations also perform well in house-price/CPI panels, under assumptions that do not automatically cover strong stock common factors. Skeptical Bayesian financial studies can conclude no or weak predictability.

Recommended bounded path, not an execution-ready preregistration:

1. Declare one frozen standalone source (concrete proposed choice: latest variable-count38-input Lasso) and diagnose its deviation from the calibrated-market CDF. With F_lambda=F0+lambda(F1-F0), loss difference is exactly2B*lambda+A*lambda^2 for the locked quadratic score. A is mean weighted squared deviation; B is the uncentered weighted mean product with market indicator error, not necessarily covariance. If B>=0 no positive contraction improves that sample; B<0 allows sample improvement but is not prospective evidence. Use original nodes/weights, matched rows, legal prequential predictions and existing temporal blocks. Audit historical baseline calibration to avoid applying a later fitted map backward. Do not select a deployment weight from exposed evaluation. This is a prediction-only ensemble diagnostic, not a replacement standalone model.

2. Only if direction is stable, consider one scalar Bayesian blend. Astra proposed an illustrative prior0.5*delta0+0.5*Beta(1,9); its numbers are assumptions, not estimated optimal values or a frozen protocol. A generalized posterior may target twCRPS, but dependence-aware temperature calibration must first be specified from training-only simulations or suitable temporal blocks. One-dimensional quadrature is cheap. Posterior-mean CDF equals the blend at posterior-mean weight; prior mass at zero does not force that final forecast to equal the exact market null. Keep null and unblended source explicit. No feature, segment, dynamic-weight or hyperparameter sweep.

Stop if direction changes sign, the fixed follow-up fails the locked development comparison, apparent success is confined to one period, or prior/temperature requires post-result tuning. Positive exploratory results still need separately authorized untouched or prospective assessment. Protected2024-2025 and2026 remain sealed.

Do not model the25 nested threshold indicators as25 independent outcomes. One continuous return or mutually exclusive bin likelihood addresses within-return dependence only; overlap and cross-sectional common shocks remain. A composite likelihood or generalized posterior must be labelled and calibrated appropriately. Repeated examination of2020-2023 remains a limitation regardless of Bayesian formulation.

Deliverables contain ranked methods, primary links, assumptions, computational burden, algebra and stop rules. Scope: research only; no MIO, registry or production changes, model fits, full tests, commits or pushes. Prior evidence hashes unchanged. Canvas passed installed SDK typecheck with a React ambient shim; browser report/detail interaction visually checked. Canvas runtime interaction itself not exercised.

## Sources

- Research/evidence: /home/russell/data/index_options/mlp-learning-audit-20261103/astra-bayesian-research
- Canvas: /home/russell/.cursor/projects/home-russell-dskit/canvases/astra-bayesian-weak-signals.canvas.tsx
- Browser: http://localhost:8765/astra-bayesian-research.html

- Casarin et al.: Bayesian calibration of option-implied densities: https://arxiv.org/abs/1409.1956
- Bassetti et al.: Bayesian predictive-density calibration and combination: https://arxiv.org/abs/1502.07246
- Piironen and Vehtari: regularized horseshoe and sparsity information: https://arxiv.org/abs/1707.01694
- Stan: hierarchical regression: https://mc-stan.org/docs/stan-users-guide/regression.html#hierarchical-regression
- McAlinn and West: dynamic Bayesian predictive synthesis: https://arxiv.org/abs/1601.07463
- Bissiri, Holmes and Walker: updating beliefs using loss functions: https://arxiv.org/abs/1306.6430
- Gneiting and Ranjan: combining predictive distributions: https://arxiv.org/abs/1106.1638
- Pesaran, Pick and Timmermann (2026): forecasting with panel data: https://onlinelibrary.wiley.com/doi/full/10.3982/QE2589
- Koval et al.: Bayesian reconciliation of return predictability: https://arxiv.org/abs/2212.02239
- Cawley and Talbot: overfitting in model selection: https://www.jmlr.org/papers/v11/cawley10a.html
- Stan: cross-validation FAQ: https://mc-stan.org/loo/articles/online-only/faq.html
