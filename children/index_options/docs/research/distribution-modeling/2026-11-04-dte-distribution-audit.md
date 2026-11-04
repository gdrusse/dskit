## Question

Does the output CDF model stock price, what distribution describes 2016–2019 by DTE, and does M53 match that shape?

## Finding

Yes: the physical terminal split-adjusted price distribution is represented through Z = log(S_T/S_0) / reference_scale. The reference is max(trailing22 RMS daily log return,0.001) times sqrt(planned trading sessions). For a strike K on the same price basis, priceCDF(K) = F_Z(log(K/S_0)/reference_scale). This is not a dividend-total-return label.

The exact-expiry panel has488310 unique2017–2019outcomes/118stocks. It has no2016rows. A separate2016price-only supplement contains1022942observed exact-calendar-horizon returns/118stocks, startingFebruary4 after22-return warmup. Those endpoints are not listed expiries and are never pooled into the primary family comparison.

Normal, Student-t and skew-normal descriptive families were fitted on316525outcomes with both quote and settlement before2019. They were checked on160809settled2019outcomes. The10976late2018quotes settling2019stay in the full descriptive panel but are excluded from both family-fit and2019-origin check samples. Student-t had best mean2019negative log likelihood in all8DTEbands and49/51exactDTEgroups with earlier fitting history. Sparse6/20DTEgroups favored skew-normal;55DTE had116outcomes only in2019 and no family fit. This identifies the best of three simple descriptions, not a true universal law or a replacement predictive model.

Pooled standardized returns are negatively skewed and heavy-tailed. Excess kurtosis decreases from14.19 at1–7days to1.78 at50–60days (normal:0); fittedt degrees of freedom increase from3.62 to6.10. Within-stock nonoverlap checks retain positive excess kurtosis:6.56 and1.53 respectively. Cross-stock shocks and pooled heterogeneity remain. Equal-stock CDF sensitivity and raw-log-return moments are retained in the report.2016shows the same broad heavy-tail pattern on a different sample.

The savedMLP has3Gaussian mixture components onZ. M53 averages quantiles fromMLP, calibrated market, pooled and per-name empirical distributions. Its final CDF is not restricted to one normal, Student-t or lognormal family. The13savedCDFknots are atZ=-3..3; the existing diagnostic interpolator closes tails linearly at-7/+7, which is a representation convention rather than an observed tail law.

M53shape assessment uses only77581matched2019fold1val forecasts with fitted=true and marketeligible=true,92stocks,March6–December26. No later-fold fit predictions are mixed in. Its largest average-CDF discrepancy across13thresholds is1.04175percentagepoints. AtZ<=-2 it predicts3.86296% versus4.86846%observed; atZ>+2 it predicts4.83197% versus4.04609%observed. PITbelow5%=5.08501%, PITabove95%=4.00614%. Per-DTE curves and checks are displayed. Family-comparison and M53cohorts differ and are never presented as a direct leaderboard.

Conclusion: M53 broadly captures the marginal shape but has a moderate downside calibration gap in this development sample. A conditional forecast need not share its pooled historical distribution. These diagnostics do not establish that forcing a Student-t family improves predictions.

## Verification and limits

Independent correctness and tests/integration reports accompany exact code/input/result hashes. Analysis is a descriptive artifact, with no core behavior or model change.2019is repeatedly observed development warmup; no2020+outcomes were returned by data filters. No iid p-values, new promotion, MIO or production change. Source publication times, adjustment vintages and future-path cohort assumptions remain inherited and uncertified. Actual peakRSS2396.75MiB exceeded the2GiB estimate but stayed well within measured21.3GiB WSLavailable memory; saved outputs are small. The initial empty55DTEfit refusal was fixed by reporting its lack of earlier history, with unchanged family choices.

## Sources

/home/russell/data/index_options/mlp-learning-audit-20261103/dte-distribution-audit/protocol.json
/home/russell/data/index_options/mlp-learning-audit-20261103/dte-distribution-audit/candidate-lock.json
/home/russell/data/index_options/mlp-learning-audit-20261103/dte-distribution-audit/results.json
/home/russell/data/index_options/mlp-learning-audit-20261103/dte-distribution-audit/correctness-review.json
/home/russell/data/index_options/mlp-learning-audit-20261103/dte-distribution-audit/tests-review.json
/home/russell/.cursor/projects/home-russell-dskit/canvases/DTE-distribution-audit.canvas.tsx
https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html
https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.skewnorm.html
