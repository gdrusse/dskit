# First MIO backtest readiness and final predictive-model recommendation

## TL;DR

Do not run the first real strategy backtest yet. The MIO optimizer is implemented and tested, and `lean-pooled-h10` is the best current **development** LightGBM candidate, but the final tuned model, real forecast bundle, calibrated uncertainty, and stateful execution replay do not exist. Running now would require made-up inputs or would spend data reserved for the final test.

Recommendation: keep `lean-pooled-h10` as the provisional finalist, align and run the finalist-only HPO on that exact lean feature mask, persist the winning parameters and fitted estimator, then build and verify the real bundle/replay path. Keep every observation from 2026-03-01 onward untouched until that procedure is frozen.

## Execution contract

This was a readiness review on 2026-09-08, not a backtest. The requested operation was to combine the MIO on `main`, the locked testing calendar, and the selected LightGBM feature/HPO evidence into a first backtest pipeline and run it. Inspection stopped before creating a pipeline because the inputs needed for an honest run are absent.

No market-data pipeline, fit, HPO, backtest, simulator, MIO solve, or lockbox read ran during this review. No observation dated 2026-03-01 or later was read by a model or evaluation.

Evidence reviewed:

- `main` commit `7d9d6108bfbc589e74b23c9e0055dde93efe1963`, which merges ADR-0111's scoped MIO implementation.
- `configs/run-p16-feature-mask-zoo.json`, SHA-256 `41264b73950dbbab967c995aedd4e22e430d00e607ae6736e7d7fa3a25ae1469`.
- selected candidate `lean-pooled-h10`, document hash `0f483677cf7aea5c16563c7db8439cdc8e34736b381431c4c556816e45c7f7a5`.
- `configs/run-final-hpo.json`, SHA-256 `604936371b4c134f8898b75833d5dd15ef5f8ad5cd8bbf3f3ef50bed210a0120`.
- `configs/program-calendar.json`, SHA-256 `d5409f516e28a7ecea6101a5ccae0fc281c1b8eab7f3d0a052906314b35e222c`.
- P16's sealed prediction, comparison, inventory, and final-gate artifacts recorded in `docs/memos/p16-feature-mask-and-final-gate-results.md`.

## What is implemented

The merged `EquityKellyMIO` is a fail-closed, one-decision-tick capital optimizer. Given a valid forecast bundle, current account state, and names that passed the statistical gate, it chooses integer share targets under cash, buying-power, position, cardinality, false-discovery, CVaR, cost, and utility constraints. Its synthetic demo is a solver smoke test, not a market backtest.

P16 completed 100 of 100 development folds across five feature masks. The lean mask had the best mean path score, `0.0065199151`, with fold standard deviation `0.0023114253`. Its post-selection guardrails retained 44 contiguous horizons across 11 of 25 stocks. Those same development folds selected the mask and supplied the guardrail evidence, so the result is explicitly not an untouched performance estimate.

The predictive model was refreshed every 63 calendar days in P16. Each fold used a trailing 730-day training window, a 5-day embargo, and a 63-day validation block. This is the only refresh cadence backed by the existing sealed predictions; changing to monthly retraining would be a new experiment.

## Why the backtest did not run

Four independent gaps block a correct run:

1. **No final tuned model artifact.** `run-final-hpo.json` is declared but has no completed run directory or result. P16 performed four inner HPO trials per outer fold, but it saved fold predictions rather than one frozen winning parameter set and fitted estimator. The existing final-HPO document also predates the lean-mask result and therefore does not train the exact recommended feature set.
2. **No real MIO forecast bundle.** Only `SyntheticMioSource` publishes the MIO's required `bundle` and `portfolio` ports. There is no real publisher for expected gross returns, calibrated false-signal bounds (`pi_upper`), timestamp-aligned joint return scenarios, and account state.
3. **Prediction units are not tradable returns.** P16's `yhat`, `y`, and `mu` are volatility-scaled, SPY-residual label units. Feeding them directly to the MIO as fractional returns would be an economics and units error. They must be causally transformed back to return units, with the market-reference policy declared.
4. **No stateful execution replay.** The MIO returns one tick's target inventory and trades. The repository has no child replay that applies next-bar execution, fills, costs, holding periods, exits, overlapping signals, and account roll-forward across ticks. Mixed 1-10-step horizons make that omission material.

The MIO's risk numbers in `run-mio-demo.json` are illustrative, not approved trading policy. Using them in a result labeled as a strategy backtest would overstate readiness.

## Final predictive-model recommendation

Use pooled LightGBM with the `lean-pooled-h10` feature mask as the provisional finalist. It is the current best-supported choice because it led the five-mask P16 comparison, remained the simplest selected candidate, and retained positive contiguous development evidence across 11 stocks. Do not call it the final model yet.

Before freezing the model:

1. Replace the stale finalist-HPO LightGBM template with the exact 33-column-drop lean mask and the already-declared 24-trial purged search. Do not reopen architecture or feature-mask selection.
2. Train through 2025-11-30, preserve the 2025-12-01 embargo, and use 2025-12-02 through 2026-02-28 only for finalist HPO, as the calendar declares.
3. Persist the chosen hyperparameters, feature schema and order, category encoding, training-data identity, model binary, software versions, and deterministic prediction checksum. A score without the fitted estimator is not a releasable model.
4. Refit the frozen winner through 2026-02-28 without another search. This creates the model that enters confirmation.
5. Keep the 63-calendar-day refresh schedule for the first developmental replay because it matches the evidence. Treat a roughly monthly schedule as a later controlled comparison, not an untested default.

The current serving caps remain development-only: `ADBE 4; CIEN 5; LITE 3; LLY 2; LRCX 10; LULU 2; MSTR 6; NOW 5; PANW 1; TER 3; XLK 3`. For the first real replay implementation, start with a common one-step horizon and non-overlapping decisions. That removes ambiguous mixed-horizon exits while the accounting loop is being validated; broader caps can be added only after horizon and expiry semantics are explicit.

## Training, tuning, and evaluation schedule

The project calendar already defines the windows. The important distinction is that **nested development tuning is complete, but final hyperparameter tuning is not**.

- **Completed development selection — 2022-05-06 through 2025-10-16.** P16 ran 20 non-overlapping validation folds. At each 63-calendar-day model refresh, it trained on the preceding 730 days, left a 5-day embargo, and evaluated the next 63 days. Each outer fold performed four inner HPO trials using only that fold's training history. This selected the lean feature mask and produced developmental predictions; it did not produce one frozen final parameter set or fitted model.
- **Final-fit extension — 2025-10-17 through 2025-11-30.** These observations may be included in the finalist training set. They are fit-only data and must not be reported as performance evidence.
- **Embargo — 2025-12-01.** Do not fit or score on this session. It separates the final training cutoff from finalist validation by more than the maximum label horizon.
- **Finalist-only HPO — 2025-12-02 through 2026-02-28.** This 89-day block is reserved for the unrun 24-trial LightGBM search. First align `run-final-hpo.json` to `lean-pooled-h10`; then train through 2025-11-30 and select hyperparameters here. This block becomes spent selection data, not an untouched test.
- **Final refit — through 2026-02-28.** After HPO chooses one parameter set, refit the frozen lean LightGBM once on every permitted observation through 2026-02-28. Persist the model and its full release identity. Do not search again during this refit.
- **Mean confirmation and uncertainty calibration — 2026-03-01 through 2026-05-31.** With the mean model frozen, first test its predictions prospectively, then use the realized residual vectors to calibrate `pi_upper`, expected-alpha uncertainty, and joint return scenarios. Do not refit the mean model or redesign the procedure from these rows. Because these rows calibrate uncertainty, they cannot also be the final test of that uncertainty product.
- **Untouched full-system simulation — 2026-06-01 through 2026-08-31.** Freeze the mean model, uncertainty bundle, statistical gate, MIO policy, cost model, and execution rules before June 1. Run the end-to-end backtest once with no retuning. Report 2026-08-01 through 2026-08-31 as the predeclared Test B subperiod, not as a second tuning set.
- **Paper/live eligibility — 2026-09-01 onward.** Begin only if the frozen full-system simulation passes its predeclared gates and the owner approves the risk and account-policy settings.

For model refreshes, retain the evidenced 63-calendar-day schedule for the first developmental and full-system designs. The final refit is a one-time release fit; once operating prospectively, retrain every 63 calendar days on a trailing 730-day window with the frozen feature schema and hyperparameters. A monthly cadence remains a future experiment and must not be introduced during the untouched simulation.

In short: the next runnable modeling step is not another broad model search. It is one 24-trial finalist HPO on the exact lean mask, followed by one frozen refit through 2026-02-28. March onward remains untouched until both the model release and replay procedure are ready.

## Recommended build and test sequence

1. Align and run finalist-only HPO on the lean mask, then persist and refit the frozen LightGBM.
2. Implement a point-in-time publisher that converts predictions to gross-return units and produces causally calibrated `pi_upper` plus timestamp-aligned joint residual scenarios.
3. Implement a stateful replay using next-bar execution, explicit spread/fee/slippage assumptions, holding and forced-exit rules, and a single rolling account ledger.
4. Run a **developmental procedure replay** only on already-spent data ending 2025-10-16, using the existing 63-day refresh boundaries. Label it `deployment_eligible=false`; use it to find software and accounting defects, not to estimate future returns.
5. After the complete procedure is frozen, use 2026-03-01 through 2026-05-31 once for mean confirmation and uncertainty calibration. These rows calibrate uncertainty and cannot also judge that newly calibrated product.
6. Freeze the full mean/uncertainty/Gate-2/MIO/execution bundle before 2026-06-01. Run the first untouched full-system simulation once on 2026-06-01 through 2026-08-31, retaining August as the predeclared subperiod diagnostic.

## Independent review

Two fresh skeptical reviews reached the same conclusion.

The temporal review found that a developmental replay of sealed P16 predictions through 2025-10-16 can be causally ordered at the existing 63-day cadence, but mask selection reused those folds and any uncertainty must use only information available before each decision. It classified any present use of 2026-03-01 onward as a critical calendar violation.

The MIO/replay review found critical missing real-bundle and stateful-replay machinery, plus major unit, calibration, horizon, fill, and owner-policy gaps. It confirmed that P16 adjusted skill probabilities must not be relabeled as `pi_upper`.

Neither reviewer found a way to produce a correct real-data MIO backtest from configuration alone.

## Reproducibility and handoff

No backtest artifact exists because no backtest ran. The worktree remained on `main` at `7d9d6108bfbc589e74b23c9e0055dde93efe1963` before this memo was added. The March-August 2026 lockbox remains untouched by this review.

Next authorized action: align `run-final-hpo.json` to `lean-pooled-h10`, add artifact persistence, review that change, and run it only through the declared 2026-02-28 cut. Do not begin the real MIO backtest until the forecast-bundle and execution-replay contracts above are implemented and reviewed.
