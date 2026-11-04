## Question

Do Astra's top recommendations now: audit feature timing, measure forecast instability, and compare one bounded ridge family against Lasso under the same validation procedure.

## Finding

Ridge reduced sensitivity to omitted validation quarters but predicted worse. No new model supports replacing calibrated market. This is retrospective development, not fresh generalization evidence.

Timing review found unresolved dependencies beyond explicit open interest. Vendor IV/delta and vendor underlying determine option features; a historical probe documents underlying timestamps after close. Raw-chain quote eligibility also filters on OI and IV, so apparently unrelated risk-neutral and liquidity fields inherit these dependencies. Conservatively withheld all 68 option-derived predictors, retaining the 38 price/calendar/liquidity fields. This was an eligibility restriction, not proof that the old edge was leakage. Actual export publication clocks remain unverified.

The 38 retained features are 22 lagged returns, two known calendar features, two momentum measures, eight realized/directional volatility measures and four trailing liquidity measures. Daily availability and adjustment vintages remain unverified. The inherited panel also conditions on completed future paths and segment-level corporate-action basis estimates; calibrated-market and old-model references share historical assumptions. Do not call this panel causally clean or deployable. No vendor data, protected outcomes or replacement cohort was fetched.

Protocol frozen before training: the same five outer fits (2019 warmup, 2020-2023 ranked), three expanding settlement-purged inner quarters, training-only preprocessing, full-training refit, exact13-node projected-CDF twCRPS, pooled row weights and strict minimum selection. No one-SE rule. Ridge searched seven normalized penalties [100,10,1,.1,.01,.001,.0001] plus null, with sklearn alpha=n*alpha. Lasso used the prior training-derived top-k ranking procedure on the same38 inputs, seven ranking penalties x38counts xseven refit penalties plus null. Fixed-ten Lasso independently tuned the same two penalties. Original ten was copied unchanged as contextual control.

All primary models and three omit-quarter sensitivity refits per method/fold were frozen before original-monitor or annual evaluation. Legacy rolling106 sensitivity models were reconstructed from its saved selections. These are fixed-training, parameter-selection perturbations; they do not measure training-sample instability, constitute independent forecasts, or authorize choosing the best alternative after scoring.

449,498 matched annual rows reproduce the original identity hash. Ridge twCRPS0.535591007676, gain over calibrated market -1.201147% (pointwise95% interval[-1.657004,-0.745291]). Variable-count Lasso0.534131437697, gain -0.925358%[-1.355062,-0.495654]. Fixed-ten Lasso0.534536597139, gain -1.001914%[-1.448392,-0.555436]. All three trail market in each annual period. Ridge trails matched variable-count Lasso by0.273260% (pointwise interval0.141363%-0.405158% worse). The unchanged original ten remains0.526740719780,+0.471135%; different input eligibility and selection make this historical context, not a matched algorithm comparison or deployability claim.

Variable-count Lasso selected3,3,8,13 inputs for2020-2023. Ridge penalties1,10,.0001,10 have effective degrees of freedom15.75,3.20,35.94,3.22 per threshold before projection, excluding intercept. The2022 ridge optimum hits the weakest tested penalty; boundary limitation recorded, no grid expansion.

Weighted RMS probability changes, pooled over annual rows and the three omit-quarter scenarios: ridge0.897pp; variable-count Lasso1.237pp; fixed ten1.254pp; legacy1061.792pp. Largest individual scenario RMS:2.765,3.577,3.931,4.098pp respectively. Legacy2021 can move4.098pp; its2022 45-feature alternative worsens loss1.319%. Thus changing counts are sometimes materially changing forecasts. Ridge mitigates this particular perturbation without restoring accuracy.

Training gains fail to persist reliably:2022 ridge gains1.588% over its training-only constant CDF in training, but loses0.044% on the later monitor;2021 variable Lasso gains1.243% in training and loses0.031% on the monitor. These gaps cannot distinguish fitted noise from changing relationships. Ridge's annual aggregate gain over constant is-0.0601%, interval crossing zero; Lasso's+0.2126% also has an interval crossing zero. Full CRPS and PIT summaries are saved; aggregate tail calibration alone does not imply conditional calibration.

Do not promote a model or expand the search. Next data priority: establish decision/publication times and reconstruct an eligible option-input pipeline and cohort, resolving vendor underlying/IV and OI dependencies before restoring predictors. This was not implemented in this bounded experiment. PCA, segmentation, universe changes and blending were deliberately excluded per the accepted Astra experimental design.

Scope: standalone research scripts in the data study directory, existing solver/scoring helpers reused, no public API or production code. A0001/A0002/A0003/A0005/A0008 preserved. Generic sklearn Ridge support inventoried. Broad sweep hit an existing git-grep timeout; a redundant broad search was stopped and scoped source reads completed. No full suite, MIO, registry, broker, commit, push or promotion. Protected2024-2025 and2026 remain sealed.

Compute: WSL only, two independent workers x2 numerical threads. Pre-dispatch shared desktop memory and Windows/WSL disk checked; Mac online but transfer/certification overhead offered no benefit. All15 inner jobs completed in roughly18-22seconds each; max worker RSS247.4MiB. Post-run resources checked. WSL/Windows RAM was not added together.

Verification: all15 cache hashes and purges, strict minima,85 model pins and pre-evaluation freeze checked; excluded columns absent from new models. Original ten scores reproduce exactly. Ridge versus sklearn synthetic multioutput predictions agree within1.2e-16, including duplicate columns; maximum Lasso KKT9.67e-12. Saved float32 CDF score reconstruction error1.15e-7. Canvas typecheck passed against installed SDK with local React ambient shim; browser report and detail interaction visually inspected. Canvas runtime interaction was not independently exercised.

## Sources

- Study protocol, source/cache pins, frozen fits, receipts, exact paired comparisons, yearly/symbol decomposition and verification: /home/russell/data/index_options/mlp-learning-audit-20261103/ridge-stability-study
- Interactive canvas: /home/russell/.cursor/projects/home-russell-dskit/canvases/ridge-prediction-stability.canvas.tsx
- Browser report: http://localhost:8765/ridge-stability.html
- Previous recommendation: docs/research/distribution-modeling/2026-11-03-astra-prediction-stability-methods.md
- theta_exact_expiry.py:33-50,386-423; cdf_study.py:2309-2320,2490-2578,3247-3322,3491,3674-3756; bar_features.py:445-450. SHA256 pins in source-pins.json.
- Historical source-time evidence: docs/research/distribution-modeling/2026-10-18-theta-chain-feature-engineering.md. This does not independently certify current export availability.
