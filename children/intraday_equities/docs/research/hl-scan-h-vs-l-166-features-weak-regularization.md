# Question

Does H=470 > L=120 mean the horizon scan is backwards? Which features did LightGBM see? Was there regularization or real feature selection, or did we fit noise?

# Finding

H and L are different axes. H is the **label lead** (minutes of RTH tape until the return we score). L is the **count of 1-minute `ret_lag_*` columns**. H > L is not a swapped pair; it is “predict ~470 minutes ahead from 120 one-minute lags.” That *is* an odd memory/horizon ratio **if those lags were the model’s only history**. They were not.

## What was actually fit

Run `intraday-equities-hl-scan-2026-08-30-c60b2910` (`configs/run-hl-scan.json`). Features were built at `scan.lookback_stop=120`, not `universe.lookback=30`. The H scan fit **166 columns** on ~666k train / 15.7k val rows (5-minute grid, five tradable names). The L scan then reused the same rows at locked H=470.

Val Spearman IC was **negative at every L tried** (train IC ~+0.39). Peak |val IC| = 0.0825 **at the grid cap L=120**. 1-SE shortest L = 120 because no shorter L was within 1 SE of that peak (next |IC| ≈ 0.066). L was not an interior optimum; `lookback_stop` forbade trying L>120 even though the rule text says `min(2H, lookback_stop)` and 2H=940.

The go rule uses **|IC|**, so a negative IC still “goes.” If the sign is real it is mean-reversion on that val window (Dec 2025–Feb 2026), not a momentum lock.

Later framework HPO on the 28 keep columns reported val IC **+0.076**. That is a **sign flip**, more val rows (19.2k; fewer NaN columns), and 50 TPE trials that **maximized val IC**. Do not read it as confirmation of the scan.

## Regularization

Mild tree constraints only. `universe.scan.estimator_params`:

- `n_estimators=200`, `learning_rate=0.05`, `num_leaves=31`
- `min_child_samples=80` (weak vs 666k rows)
- `subsample=0.8`, `colsample_bytree=0.8`
- **no** `reg_alpha` / `reg_lambda` / `max_depth` / `min_split_gain`

H selection used the **full 166-column** LightGBM each lead. `_model_ic` only *reports* the top-8 gain names; it does not refit on them.

## Feature selection

After L was locked, `_keep_by_importance` kept columns until 95% of train gain (`keep_frac=0.95`) or weight ≥ 5% of max (`keep_tau=0.05`). Calendar TOD/DOW/month and every `industry_*` are **forced in**. That is a loose keep, not a sparse penalty.

All 120 `ret_lag_*` were dropped. Reported top-8 at every L was dominated by `overnight_gap` and 1-session / 3-session vol-range-RV (`rv_3s`, `range_3s`, `vol_3s`, `rv_1s`, …). No lag ever appeared in those top-8 strings.

Named L=120 is therefore **not** the surviving memory. Kept scale windows go out to **`ret_3s` / `rv_3s` = 1170 RTH minutes** (3 sessions), which is **longer than H=470**. The user’s “H longer than L feels backwards” is right about the *names*; the keep set is the opposite (3-session features predicting a ~1.2-session lead).

## Features considered (166)

`session_feature_names(lookback=120, scales, reference=[SPY], industries)`.

**1-minute lags (120), all dropped**

`ret_lag_0` … `ret_lag_119` (tape-local; do not cross a session gap).

**Per scale, five fields each** (`ret`, `rv`, `range`, `vol`, `amihud`):

| tag | width (RTH minutes) | kept |
|---|---|---|
| 5m | 5, same session | all 5 dropped |
| 15m | 15, same session | all 5 dropped |
| 60m | 60, same session | keep `ret_60m`, `rv_60m`, `vol_60m`; drop `range_60m`, `amihud_60m` |
| 1s | 390 (one session), may cross close | all 5 kept |
| 3s | 1170 (three sessions), may cross close | all 5 kept |

**Session / calendar / static**

| name | kept? | notes |
|---|---|---|
| clv | drop | close location in the bar |
| minutes_from_open, minutes_to_close | keep | always-keep |
| tod_sin, tod_cos, dow_sin, dow_cos, month_sin, month_cos | keep | always-keep |
| is_first_rth, is_last_rth | drop | |
| overnight_gap | keep | top gain every L |
| session_gap_days | keep | |
| after_holiday | drop | |
| ref_ret_SPY, residual_SPY | drop | SPY was on the tape for residuals only |
| industry_{consumer,energy,financials,healthcare,tech} | keep | always-keep |

**28 keep names** (pinned in `universe.keep_features`): `ret_60m`, `rv_60m`, `vol_60m`, `ret_1s`, `rv_1s`, `range_1s`, `vol_1s`, `amihud_1s`, `ret_3s`, `rv_3s`, `range_3s`, `vol_3s`, `amihud_3s`, `minutes_from_open`, `minutes_to_close`, `tod_sin`, `tod_cos`, `dow_sin`, `dow_cos`, `month_sin`, `month_cos`, `overnight_gap`, `session_gap_days`, plus five industry one-hots.

`ret_*` **were** used: `ret_60m`, `ret_1s`, `ret_3s`. What was unused is **`ret_lag_*`** (and 5m/15m returns).

# Sources

- `children/intraday_equities/configs/universe.json` (`horizon`, `scan`, `keep_features`, `scales`)
- `children/intraday_equities/configs/run-hl-scan.json`
- `children/intraday_equities/intraday_equities/nodes.py` (`session_feature_names`, `_model_ic`, `_keep_by_importance`, `_lookback_verdict`, `HorizonScan`, `LookbackScan`)
- `pipeline_runs/intraday-equities-hl-scan-2026-08-30-c60b2910/carry.json` (L curve + keep list; H per-lead records were not retained)
- `docs/decisioning/decision-hl-scan.md`, `decision-framework-hpo.md`
