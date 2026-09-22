# P19 model zoo, lag study, and final HPO results

## TL;DR

LightGBM beat the MLP and GRU across the same 20 development folds. Five return lags were retained because 10 and 20 lags changed mean score by only `0.0000059907` and `0.0000103332`. Final per-horizon HPO completed all 60 heads and 1,440 trials; its validation path score was `0.003880724`. This is model-development evidence, not a trading backtest or deployment approval.

## Execution contract

Runs completed on 2026-09-22 with no data after `2026-02-28`. All candidates used only the exact 91 Gate-3 names and each horizon's eligible subset, totaling 547 stock/horizon cells. The lag study used 20 ordered walk-forward folds. Final HPO used the fixed five-lag, no-symbol LightGBM design, a 730-day training window ending `2025-11-30`, and validation from `2025-12-02` through `2026-02-28`.

Each of the 60 horizon heads scored 24 deterministic candidates on an inner 63-day validation slice with a five-day embargo. Selection maximized squared-error improvement over the training-only mean and applied the one-standard-error simplicity rule. Search dimensions were learning rate, leaves, minimum child samples, and L1/L2 regularization.

## Model and feature selection

The architecture zoo means were LightGBM `0.0074753919`, GRU `0.0066319459`, and MLP `0.0044218032`. Their fold standard deviations were `0.0025680536`, `0.00412242`, and `0.00935022`; fold wins were 13, 4, and 3. LightGBM was therefore the clear accuracy and stability winner.

The LightGBM lag study produced:

| Return lags | Mean score | Fold SD | Worst fold | Fold wins |
|---|---:|---:|---:|---:|
| 5 | 0.0074753919 | 0.0025680536 | 0.0007163381 | 8 |
| 10 | 0.0074813825 | 0.0025585653 | 0.0007703258 | 2 |
| 20 | 0.0074857251 | 0.0025993232 | 0.0006762207 | 10 |

Twenty lags improved the mean by only `0.0000103332` versus five, while slightly worsening variability and the worst fold. Five lags were retained as the simpler, more stable feature set.

## Final HPO result

All 60/60 heads completed and every ledger contains 24 trials, for 1,440/1,440 recorded trials. The one-standard-error selections collapsed to five parameter sets:

- 45 horizons: learning rate `0.003`, leaves `4`, minimum child samples `2000`, L1 `0.1`, L2 `100` — horizons 11, 14–20, 22–30, 32–33, and 35–60.
- 8 horizons: `0.01`, `4`, `1000`, `1`, `100` — horizons 1, 5, 8–10, 13, 21, and 34.
- 3 horizons: `0.03`, `4`, `4000`, `1`, `1000` — horizons 2–4.
- 2 horizons: `0.03`, `4`, `2000`, `0`, `100` — horizons 6–7.
- 2 horizons: `0.003`, `4`, `2000`, `0.1`, `10` — horizons 12 and 31.

The mean selected inner score was `0.0039550625` (range `0.0019642040` to `0.0078602079`). The final held-out aggregate covered 91 stock paths and 547 eligible heads: path score `0.0038807240`, mean validation IC `0.0812446879`, minimum common origins `612`, and worst stock/horizon score `-0.0297309832`. The negative worst cell is material: aggregate skill does not mean every eligible cell improved.

## Failures, limits, and deliberately unrun work

The staged materializer first refused because this final document intentionally has no walk-forward schedule. A first direct all-history attempt exceeded the 17 GiB WSL limit. The successful config-only rerun restored the zoo's locked 730-day training width; production code was not patched.

The final score is one December–February validation period after architecture and feature choices were made on earlier development folds. No trading-cost backtest, portfolio construction, untouched post-`2026-02-28` test, final refit, paper run, or deployment was performed. The sklearn feature-name warnings were cosmetic: the named five-lag mask was verified as preserved inside `ColumnSubsetEstimator` during every trial and refit.

## Reproducibility and handoff

- Fixed zoo: `configs/run-p19-eligible-fixed-model-zoo.json`, identity `0fba2af38a864dcc89bbd5aa4798ad01cdbc778a317fc0f196c0d4c14cbe9ce7`; summary `pipeline_runs/p19-shared-feature-fixed-model-zoo-walkforward-2026-02-28-0fba2af3`.
- Eligibility sources: P12 digest `098b21eaef6ee0260753d4f981ca2337bccae406b9efd394284d9b180ba03bd0`, P18 onward digest `c5c41c06dfebf26beedaee9079dd1362d98c7a0d0659a399ab30a2c1f0e0605b`, and P18 first-half digest `49e5a3dc9e7b9bfaf3ea2891f721b53b96bdb44bddbe89fc93b05fe643c0ae62`; together they yield exactly 91 names and 547 stock/horizon cells.
- Lag config: `configs/run-p19-lightgbm-lag-feature-study.json`, identity `8480ed65485bd3994d501247f7cabcd9578c5e84f198a5b2c83ff814694cb1eb`; summary directory `pipeline_runs/p19-lightgbm-lag-feature-study-walkforward-2026-02-28-8480ed65`.
- HPO config: `configs/run-p19-final-hpo.json`, identity `3186ff8ad272a4ddd662f20a854f4bbec7664385a24610fc0e60f77b31f6c5ae`; run directory `pipeline_runs/p19-final-hpo-2026-02-28-ae703bac`.
- HPO command: `python -m dskit.pipeline run configs/run-p19-final-hpo.json --asof 2026-02-28 --adapter intraday_equities`.
- Verification: both configs validated, the HPO plan had no warnings, every run node finished `ok`, and the process exited `0`.

Recommended next action: backtest the 60 horizon-specific LightGBM heads with the exact eligibility matrix and realistic execution costs, without reopening model or feature selection.
