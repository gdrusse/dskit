## Question

Can a sparse linear distribution predictor identify useful features and improve on calibrated market, independently of MIO?

## Finding

A new ten-feature Lasso gains 0.4711% on the locked 13-node twCRPS versus calibrated market over the same 449,498 eligible 2020–2023 observations. Annual gains are 0.5673%, 0.7285%, 0.3333%, and 0.2554%. The expiry/symbol two-way 95% pointwise interval is [0.0864%, 0.8559%]. All106 Lasso gains 0.3874% (3/4 years); current65 Lasso gains 0.3644% (4/4).

The strongest recurring fitted inputs are 22-session realized volatility, ATM implied volatility, relative trading volume, expiry timing and market-implied tail shape. Ten features are selected separately before each forecast year, using historical data only. Importance measures standardized coefficient influence, not causal effects.

Ordinary Lasso fits 25 threshold indicators; seven fixed penalties plus the intercept-only null use each original historical monitor. The top-ten supplement was specified before any annual scoring. Fifteen models, including the unranked warmup, were hashed before evaluation. Training and monitoring labels obey their settlement boundaries; A0001–A0003, A0005 and A0008 are preserved. Source panel SHA-256: 186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144.

This is retrospective development, not promotion: validation years were already exposed, intervals do not adjust for repeated searches, and raw market still wins in 2022. The ten-feature gain over the 106-input MLP is 0.2324%, with an interval spanning zero. Lasso's direct threshold objective differs from the MLP's continuous objective, so this does not isolate architecture. No protected outcomes or MIO outputs entered evaluation.

## Sources

Canonical WSL evidence: /home/russell/data/index_options/mlp-learning-audit-20261103/lasso-study/ (protocol.json, fit-results.json, models/, results.json, evaluation.parquet, feature-importance.csv, verification.json). Numerical solver checks passed; source baseline scores reproduced within 1.2e-16 and all 449,498 forecast keys matched the preceding audit. The first LARS attempt stopped before scoring; standard coordinate descent solved the same L1 objective and passed original-statistics optimality checks.
