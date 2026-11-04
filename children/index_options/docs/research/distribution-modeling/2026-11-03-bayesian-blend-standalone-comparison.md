## Question

Does one frozen Lasso direction improve calibrated market when shrunk, and how does a Bayesian blend compare with the best standalone ML forecasts?

## Finding

Implemented the bounded prediction experiment. One source was pinned before inspecting curves: variable-count Lasso from the 38-input study. Its exact loss curve has a beneficial direction in each of 2020–2023. The pooled hindsight optimum is 31.96% Lasso, gaining 0.262%; that descriptive weight was never deployed.

The Bayesian rule was frozen before those results: half the prior mass at zero, half Beta(1,9); a scalar generalized posterior targeting summed exact 13-node twCRPS. Its temperature matches scalar curvature to whole-origin-date score variance using 120 calendar-day Bartlett HAC lags. Fixed minimum history is 480 days of origin span and 240 observed dates. Annual weights use only earlier origins whose outcomes settled before January 1. Temperature and prior were not searched.

Lasso shares are 0%, 20.83%, 27.65% and 25.65%. The 2020 history is insufficient, so it uses exact market. On 449,498 matched rows, Bayesian twCRPS is 0.528524537912 versus market 0.529234126606: gain +0.134078% (pointwise paired expiry/symbol 95% interval +0.040239% to +0.227917%). Annual gains are 0%, +0.329160%, +0.035189%, +0.166681%. A simple fitted blend gains +0.139078%; Bayesian's direct difference is unresolved. For 2022, historical fit gain of 0.304% falls to 0.035% the next year. This does not establish absence of overfitting.

The user clarified that the principal comparator is the best standalone ML model. A separate read-only comparison preserves all fits and checks the existing comparable Lasso, tree and neural inventory. Original ten-feature Lasso remains best observed: twCRPS 0.526740719780, gain +0.471135% versus market. Bayesian loss is 0.338652% higher (paired 95% interval 0.013840%–0.663464% worse), and is higher in all four years on point estimates. The best ML-only MLP seed ensemble gains +0.289454%, MLP106 +0.239253%, and LightGBM +0.163976%; their direct differences from Bayesian include zero. M53's standalone MLP gains +0.119848%; Bayesian's direct gain is only +0.014248%, also unresolved.

This implementation learns a Bayesian blend weight; it does not fit a standalone Bayesian predictor. Earlier standalone models use broader inputs and different selection procedures. These are outright forecast comparisons, not an isolated test of Bayesian estimation. A weaker standalone forecast can still add complementary information to M53, but that incremental combination test has not been run.

All five market calibration maps reproduce exactly from earlier fit rows. Source scores reproduce within 1e-13; identities, settlement ordering and model pins pass. Fifteen focused tests cover score algebra, valid CDFs, quadrature, zero atom, row duplication, loss rescaling, cutoff exclusion and exact fallback. A real 5.55e-17 rounding defect initially counted 2020 as a winning year; it was corrected to an exact tie, with the failed assertion and superseded artifacts retained. Exported research forecasts cover the same 449,498 rows. Independent review evidence is retained with immutable candidate hashes.

These years are repeatedly exposed and also selected this branch. Pointwise intervals do not adjust for the accumulated search or all overlapping-expiry dependence. Feature availability, revisions and cohort construction remain uncertified. HAC temperature is a research approximation, not calibrated posterior certainty. CRPS13 and node Brier are secondary; full-distribution CRPS/PIT were not established. Protected years remain sealed. No source refits, MIO, production API, model promotion or full test-suite run.

## Sources

- /home/russell/data/index_options/mlp-learning-audit-20261103/bayesian-blend-study/: protocols, diagnostic-results.json, weight-fits.json, bayesian-results.json, standalone-comparison.json, verification.json, exported forecasts and retained independent review reports.
- Existing frozen models and saved forecasts are individually pinned in the study and standalone-comparison manifests. Existing GridCDFMethod linear-pool capability was inventoried; original exact scoring and paired comparison were reused.
- Astra's research and training-only temperature recommendation. Scalar information matching: https://academic.oup.com/biomet/article/106/2/465/5385582. Bartlett HAC: https://www.nber.org/papers/t0055.
- Browser report: http://localhost:8765/bayesian-blend.html.
- Canvas: /home/russell/.cursor/projects/home-russell-dskit/canvases/bayesian-prediction-blend.canvas.tsx.
