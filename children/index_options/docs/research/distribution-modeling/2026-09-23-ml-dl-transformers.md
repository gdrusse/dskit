## Question

Which standard ML, deep learning and transformer/foundation models have the
best academic and industry evidence for forecasting the 21-day S&P 500
return distribution in standardized z, given ~300 non-overlapping windows?

## Finding

ML adds modestly and only as an overlay on the A0003 ladder. Most evidence
is daily-to-weekly realized-variance POINT forecasts (often single stocks);
almost none covers full multi-week index densities.

Ranked candidates:
1. **GBM scale model (LightGBM/CatBoost)** on next-22-day log RV from HAR
   lags, VIX, term structure, skew; plug in as the scale (the
   `relative_scale` hook), shape from FHS/empirical. Christensen, Siggaard &
   Veliyev (2023): ML beats HAR, more at longer horizons (stocks, not
   index). Rahimikia & Poon: wins ~90% of periods, not in extreme vol;
   ensemble with HAR. Realistic gain ~0-10% on scale loss; tail twCRPS
   ~0-5% (estimate, unverified).
2. **Distributional GBMs** (LightGBMLSS/XGBoostLSS, NGBoost; t or skew-t),
   trained on a proper score. Strong CRPS on generic benchmarks (Marz), not
   finance. Keep 2-3 parameters, shallow trees.
3. **Conformal recalibration (ACI / CQR)** on every rung: cheapest fix for
   PIT/Berkowitz failures; ACI paper tests stock volatility. Improves
   calibration/Brier more than CRPS sharpness.
4. **Optional last: TTM or fine-tuned TimesFM** as a monthly-scale feature,
   not the density. Brini: of 9 foundation models zero-shot, only TTM beat
   Log-HAR, narrowly; genuine information gain only at monthly horizon.
   TimesFM needs fine-tuning (Goel et al.). Return forecasts: gains "small
   and sparse".

Deprioritized: LSTM/GRU, DeepAR, MQ-RNN, TFT, N-HiTS, flows, MDNs (single
studies, point RV, high overfit at this sample size); Informer/Autoformer/
PatchTST (DLinear beats them, Zeng 2023); option-surface ML (Bali et al.
is cross-sectional option returns) until surface history is long.

Industry: no verifiable public write-ups from Man AHL, Two Sigma, JPM on ML
for monthly index densities. Optiver Kaggle (10-min RV) is irrelevant at
21 days and partly a leakage artifact (unverified recollection).

Pitfalls: purged/embargoed walk-forward (embargo >= 21 steps), HPO inside
folds, HAC/block-bootstrap DM/GW tests; align VIX and close timestamps;
rearrange crossing quantiles; conformalize neural quantiles; Mincer-
Zarnowitz to separate real gains from rescaling.

## Sources

- Christensen, Siggaard & Veliyev 2023: https://academic.oup.com/jfec/article-abstract/21/5/1680/6612759
- Rahimikia & Poon: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3707796
- Bucci 2020: https://academic.oup.com/jfec/article-abstract/18/3/502/5856840
- Brini, TSFMs vs HAR: https://arxiv.org/abs/2607.05291
- Goel et al., TimesFM volatility: https://arxiv.org/abs/2505.11163
- TSFMs on returns: https://arxiv.org/abs/2606.27100
- Marz, distributional GBMs: https://arxiv.org/pdf/2204.00778
- NGBoost: https://arxiv.org/abs/1910.03225
- ACI (Gibbs & Candes 2021): https://arxiv.org/abs/2106.00170
- Bali et al. 2023: https://academic.oup.com/rfs/article-abstract/36/9/3548/7056660
- Zhang et al. 2024: https://academic.oup.com/jfec/article/22/2/492/7081291
- Zeng et al. 2023: https://arxiv.org/abs/2205.13504
- Unverified (from memory): Kim & Won 2018; TFT (Lim et al. 2021); Optiver 1st-place method.
