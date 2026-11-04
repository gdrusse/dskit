## Question

Can eight fixed information sets predict errors in M53's issued probabilities and improve on M53 and an F0-only calibration control? This is the owner-authorized successor to the closed calibrated-market residual study (ADR-0291).

## Finding

No tested procedure improves M53. Seven information sets and calibration-only choose the exact null in all four years. The volatility-interaction procedure and the global inner-selected procedure increase pooled forecast loss by 1.388965%, from 0.525580619400 to 0.532880749283. Its 2020 loss increases 5.781608%; 2021–2023 retain M53 unchanged. All ten continuation clauses fail. Close this fixed feature/operator search; retain M53. This does not prove that every possible use of these inputs fails.

| Information set | Mean weighted CRPS | Reduction versus M53 | Annual selection |
|---|---:|---:|---|
| Pooled recent errors | 0.525580619400 | 0.000000% | Unchanged M53 in all years |
| Pooled error drift | 0.525580619400 | 0.000000% | Unchanged M53 in all years |
| Stock error memory | 0.525580619400 | 0.000000% | Unchanged M53 in all years |
| Maturity error memory | 0.525580619400 | 0.000000% | Unchanged M53 in all years |
| Probability-bin errors | 0.525580619400 | 0.000000% | Unchanged M53 in all years |
| Component disagreement | 0.525580619400 | 0.000000% | Unchanged M53 in all years |
| Volatility interactions | 0.532880749283 | -1.388965% | Correction in 2020 only |
| Combined information | 0.525580619400 | 0.000000% | Unchanged M53 in all years |

Percentages are forecast-loss reductions, not returns; positive is better. Every row is a separately inner-selected procedure with its own null fallback. All nonnull candidates were evaluated in the fixed inner blocks; zero rows are actual no-correction selections, not skipped experiments. Calibration-only equals M53, so comparisons against the two controls coincide.

The selected procedure's pointwise 95% interval for loss reduction is [-2.544854%, -0.233075%]. For the volatility hypothesis, the Bonferroni eight-comparison interval is [-3.015231%, 0.237302%]. Each control has a separate eight-hypothesis family with 5% family error budget, 0.05/8 per interval and two-sided t critical value; dependence among hypotheses is allowed. Variance clusters by expiry and symbol, subtracting their intersection. Exact-null rows have zero paired variance and no inferential interval. Adjustment does not repair repeated historical exposure.

The secondary full-distribution CRPS also worsens: M53 0.661248864230, selected 0.669815995879. PIT/reliability diagnostics are saved; overlapping horizons preclude treating usual independent-observation diagnostic p-values as confirmation.

### Frozen execution contract

449,498 identical forecasts: 2020 100,950; 2021 119,812; 2022 117,775; 2023 110,961. Identity hash `d9b0ccd654b66e537b8c1fd8148bcf70640a2f1b587abecf7400fedc4ef2bd05`. Previously fitted 2019 M53 rows are unranked history only. The target is the realized-threshold indicator minus saved final M53 probability at 13 thresholds. Existing ResidualRidgeCDF predicts the error and restores a valid curve by weighted isotonic projection.

Eight information hypotheses use a common estimator: pooled 30/90/365-day errors; changes across windows; stock memory; maturity memory; probability-bin memory; disagreement among four M53 components; four volatility inputs and fixed interactions; the combined set. Group histories use 500 pooled-prior rows. There are 28 settings per annual selection: eight hypotheses plus F0-only, three penalties each (1, .01, .0001), and exact null. Inner validation uses May–June and July–August of the prior year, expanding earlier training. All historical monitoring exclusions remain. Annual coefficients are frozen; memory updates daily using strictly earlier settlements.

All four annual selections, model states and prediction hashes froze before comparative scoring. Feature construction legitimately reads prior-settled outcomes under the frozen rule; this is not a claim that all annual labels were unread during preparation. 2024+ outcomes remain excluded. Settlement dates are the approved availability assumption; publication timing is unverified. These are repeatedly exposed retrospective years, not fresh confirmation or promotion evidence.

### Implementation and execution evidence

Reviewed scientific candidate `b25460f6038d1c39c5235079f9437726a3e7a975`; final runtime revision 3. Phase-0 review and two fresh final reviews passed with zero unresolved Critical/Major findings. The 495 focused tests passed; ten inherited warnings remain. Prior review findings were corrected before fitting. The retained minor documentation issue mentions calendar inputs, while the exact authoritative volatility definition contains 16 volatility fields and no calendar inputs. No scientific bytes changed after the final lock.

The Mac pilot computed valid forecasts but exited 1 because its measured 3,676.19 MiB peak exceeded the 3 GiB planning target. It was not silently rerun or relabeled as a pass. Its 8,192 saved predictions reproduced exactly in WSL (max difference 0); a new WSL training-parity pilot was not run because shared desktop memory was insufficient. The documented full-run placement budget was 12 GiB with two numerical threads. Matching Python 3.12.3 and numerical package versions, source bytes, public onboarding roots, executable hashes and WSL-backed mounts were verified before dispatch.

The full Mac run exited 0 in 331.05 seconds, peak 10320.02 MiB, result bytes 210,009,744. All research inputs, outputs, scratch and cache remained physically in WSL. Completion resource checks cover WSL, Windows physical memory and C: capacity, and Mac capacity; WSL and Windows RAM are not summed. No worker remains active for this run.

Independent WSL verification passed: atomic-store hashes; exact 449,498 identities and annual counts; exact baseline probabilities; reconstructed row losses for null, F0-only and selected forecasts; all method/annual means; all inner selections and 28 settings per year; every paired variance and adjusted interval; all continuation clauses. Progress order verifies all models froze before any annual scoring. This is prediction/output verification, not a second cross-host training comparison.

### Decision and next priority

Retain M53 and close this bounded batch. Before another modeling study, establish point-in-time input availability and reserve an untouched evaluation window. New work should test new information under a separately frozen design; these results supply no candidate for promotion or further tuning of these eight recipes. No merge, push, trading or protected-outcome access was performed.

## Sources

- Run root: `/home/russell/data/index_options/m53-error-memory-study-20261103`; `run` resolves to the verified WSL worker output.
- `protocol.json` SHA256 `bd80d246790f46f193fa9bf598b5a1ce4ece99e51187a8fe8181c17f92bd71fb`; `config-prepared.json` and `mac-runtime-config.json` record normalized-equivalent source/output aliases.
- `run/evaluation/report.json` SHA256 `202d3cdf406e1abc32d322ed5b4015fb359ca25d680fc079955696e533eff58a`.
- `run/freeze/models.json` SHA256 `5a98eea32c5425ec160e425a762951729fc39109c18236b01fa7951a3c3171eb`.
- `run/multiplicity.json` SHA256 `69d4664692f970e4bc435d775782dfc37595b8a7486c764dba3ecac37d8c6442`.
- `verified-summary.json`, `verify-results.py`, `verify-results.log`, `completion-receipt.json`, `final-lock.json`, both final review reports, `placement-decision.json`, `pilot-restoration-parity.json`, `resources-after-full-run.json`, `resources-after-full-windows.json`.
- Results view: `/home/russell/.cursor/projects/home-russell-dskit/canvases/calibrated-market-error-study.canvas.tsx`; TypeScript check passed, earlier study retained separately.
