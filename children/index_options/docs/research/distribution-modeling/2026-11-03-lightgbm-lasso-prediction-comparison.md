## Question

Does conservative LightGBM generalize better than Lasso on its exact ten selected inputs?

## Finding

Prediction only, same 449,498 annual 2020–2023 observations: LightGBM gains 0.164% versus calibrated market (95% paired expiry/symbol interval −0.246% to +0.574%), compared with Lasso +0.471%. LightGBM is 0.309% worse than Lasso (gain interval −0.515% to −0.102%). It beats calibrated market in 2/4 years; Lasso in 4/4.

The same historical bands select common rounds 143, 18, 42 and 299. The first three stop after 40 rounds of no material validation improvement while training improves. Forecast-2023 reaches the fixed 300-round ceiling with validation still improving slightly. All five models, including unranked 2019 warmup, were frozen before annual scoring. No parameter sweep or MIO outcome enters this experiment.

Lasso's ten inputs were a preset cap after ranking standardized coefficient norms across 13 scored thresholds, not an automatically discovered count. Unrestricted Lasso retained 62/34/55/54 inputs. All ten remain active in each reduced fit; the four lists span 17 fields. Delta/gamma/vega open-interest features were candidates but all 25 coefficients were zero in every unrestricted annual fit; the inputs were not constant.

Ten-feature Lasso improves over unrestricted Lasso by 0.084% (95% interval −0.074% to +0.242%), driven by 2022; it loses slightly in the other three years. The lead over the 65-input control is 0.107% (−0.021% to +0.235%). Neither establishes the optimal feature count. Training improvement versus the constant forecast is 2.12–3.01%, and historical validation improvement 1.20–2.73%; distinct time periods and reuse for selection prevent interpreting the gap as a pure measure of overfitting. Exposed annual validation remains exploratory; 2024–2025 and 2026 remain sealed.

## Sources

Canonical evidence: `/home/russell/data/index_options/mlp-learning-audit-20261103/lightgbm-study/`: `protocol.json`, `fit-results.json`, `results.json`, `verification.json`, `lasso-selection-audit.json`, `lasso-size-comparison.json`, `execution.json`, per-year matched scores and saved models. Verification reproduced primary scores within 2.3e-16, preserved every reference score, replayed stopping exactly, confirmed feature/date parity and finite monotone CDFs. Worktree base f6e4767492c2e52a4d1393b6ab82e462c6b80ead; LightGBM 4.7.0. Followed locked prediction protocol A0001/A0002/A0003/A0008; no protected holdout or production change.
