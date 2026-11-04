## Question

How does the compact nested-selected Lasso compare outright with full historical M53, prediction only?

## Finding

On the same 449,498 identities in 2020–2023, full M53 has primary twCRPS 0.525580619 versus compact Lasso 0.527049634. M53 reduces error by 0.278724% relative to compact Lasso (paired expiry/symbol 95% interval +0.037186% to +0.520262%). M53 wins 2020, 2021 and 2023; compact Lasso wins 2022. Annual M53 gains versus compact Lasso are +0.870800%, +0.566774%, −0.442745%, +0.151752%. Relative to calibrated market, M53 gains +0.690339% versus compact Lasso +0.412765%.

This is M53's complete rolling quantile blend of MLP, calibrated market, pooled empirical and per-name empirical forecasts, with 21-day weight updates, compared against the annual Lasso refits as saved. It does not isolate architecture or update frequency. The compact Lasso improves +0.293268% against M53's historical MLP component alone, but its interval −0.086228% to +0.672765% includes zero. M53's overall win does not establish the incremental value of that MLP. The historical MLP component differs from the 106-input challenger and current selected65 quarterly cohort.

No fitting, MIO, portfolio outcomes or protected data were used. Same A0002 exact score and A0005 clustered comparisons. All 449,498 joined M53 rows were fitted, keys and targets match, and saved M53 scores reproduce within 1e−12. Previously exposed 2020–2023 makes this exploratory evidence; no multiplicity correction or promotion.

## Sources

`/home/russell/data/index_options/mlp-learning-audit-20261103/nested-selection-study/m53-comparison.json` and `compare_m53.py`; historical forecasts at `/home/russell/data/index_options/theta-strike-screen-20261018/scores/edge-method-53-qa-mlp64-rolling-by-dispersion/forecasts.parquet`. Source fit and consumed-column hashes and matched identity hash are in the comparison JSON. Updated existing nested feature-selection canvas and browser report; TypeScript check passes.
