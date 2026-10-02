# Plans

Full prior text (stages, runbooks, evidence, hashes): [archive-2026-10-02.md](archive-2026-10-02.md).

## 1. What it is

`configs/workflow.json` is the single entry point of the 7-step study (1 target dates, 1b features, 2 availability, 3 holdout/folds, 4 forward selection loop, 5 model zoo, 6 HPO, 7 evaluate and report). Edit only its `args`. `layout` holds output patterns, `registry` the template and command per step, `steps` only references. Nothing else names a ticker, number, field or file.

## 2. How to run

Verified end to end for QQQ and IWM (lanes run in order, about 15 min wall, 2.5 GB peak, GPU). Run from `children/index_options` with `PYTHONPATH` set to the repo root.

```
python -m dskit.pipeline workflow configs/workflow.json --plan                  # validate, print the DAG, run nothing
python -m dskit.pipeline workflow configs/workflow.json --args my.json          # run 1 to 7 for every ticker in args.tickers
python -m dskit.pipeline workflow configs/workflow.json --args my.json --only @IWM     # one ticker (lane)
python -m dskit.pipeline workflow configs/workflow.json --args my.json --from step4@IWM # resume from a step
```

Capped form used for the verified runs (6 GiB, no swap):

```
systemd-run --user --wait --pipe --collect -p MemoryMax=6G -p MemorySwapMax=0 -p RuntimeMaxSec=21600 \
  -p WorkingDirectory="$PWD" -E PYTHONPATH="$(realpath ../..)" \
  /usr/bin/time -v python -m dskit.pipeline workflow configs/workflow.json --args my.json
```

- `--args FILE` is a JSON **deep-merged** over `args`: write only the keys that differ.
- Writers refuse to overwrite. A new run needs a fresh `work_dir` (set it in `my.json`). Rerunning the same `work_dir` skips unchanged steps by hash and reruns a failed one; if the failed step had already finished its search, move that step's study directory aside first.
- Torch deterministic CUDA needs the variables in `args.run_env`; the runner sets them for every command.
- Outputs: `{work_dir}/step*/` per step and `{work_dir}/report/report_<T>/report.md`, `report.html`, `sections/*.csv` per ticker. `{work_dir}/workflow.json` is the ledger (config hash, input and output hashes, exit code per `step@ticker`).
- Exit codes: 0 ran, 1 error, 3 halted, 5 refused.

## 3. Run for an arbitrary ticker

1. **Check the data exists** for `<T>` in the store (`args.price_source.root`): the daily price file (`underlying_prices.parquet` under the options-archive source), rows for `<T>` in the exact-expiry tables (`surface`, `lifecycle`) and `raw-chain-features`, a volatility-index series in `cboe-index-wide`, and rows in the `cdf-horizon-panel` input panel. A missing source is a named refusal at step 1 or 1b, never a silent skip.
2. **Write `my.json`** with only the three lane-keyed values (a missing key is a named refusal; `feature_order` is one shared list whose flag column follows the lane, and `expected_cells` and the reference models derive from `horizon` and feature names):

```
{
  "tickers": ["<T>"],
  "work_dir": "./pipeline_runs/<new-run-name>",
  "price_source": {"relpath": {"<T>": "<t>/underlying_prices.parquet"}},
  "vol_index": {"<T>": "<volatility index symbol>"},
  "feature_limits": {"task_features": {"<T>": "is_<T>"}}
}
```

3. **Other horizon:** set `"horizon": <days>` (and `feature_limits.max_dte` at least as large). Nothing else is typed per horizon.
4. **Other data families:** edit `families` (one spec: per family sources, fields, quality checks, ages) and `core`. Each family is availability-checked at step 2, admitted at step 3 (rate at least `split.tau`), offered to the step 4 forward loop, and its columns join the derived feature order. A family whose data is a keyed table in the store needs no code (`tests/test_new_family.py`). A family's companion columns (ages, masks) enter the model only if declared.
5. **Other data source:** change `price_source`, `options_panel`, `feature_sources`, `decision_regions` (store references) and `panel_schema` (field names).
6. **Own vol index:** `market_policy.drop_own_index` (default false) keeps a market series equal to the lane's own index; set true only after removing its names from families and `feature_order` (it refuses otherwise).
7. **Plan first:** `... workflow configs/workflow.json --args my.json --plan` must print the DAG for `<T>` with no refusal, then run (section 2).

## 4. Steps

| Step | Template (`configs/templates/`) | In | Out files |
|---|---|---|---|
| 1 | `step1-target-dates.json` | T, U, H, S, W, A, labels | `{W}/step1/target_dates_{T}_h{H}.jsonl`<br>`{W}/step1/date_counts_{T}_h{H}.png` |
| 1b | `step1b-feature-engineering.json` | T, target, O, V, windows, G, C, M, families, S, W, F, A | `{W}/step1b/input_panel_{T}.jsonl` |
| 2 | `step2-feature-availability.json` | T, panel, target, families, S, W, A | `{W}/step2/dates_{T}.jsonl`<br>`{W}/step2/combinations_{T}.jsonl` |
| 3 | `step3-holdout-folds.json` | T, dates, panel, families, holdout, tau, folds, embargo, S, W, A | `{W}/step3/fold-table_{T}.jsonl`<br>`{W}/step3/admission-and-metrics_{T}.jsonl` |
| 4 (loop) | `step4-feature-selection.json` | T, fold_table, panel, model, references, acceptance, selection, S, resolutions, bootstrap, H, W, F, V, windows, G, C, M, regions, admitted, families, core, features, sequence | `{W}/step4/core_{T}.json`<br>`{W}/step4/rounds_{T}.jsonl` |
| 5 | `step5-model-zoo.json` | T, fold_table, panel, model, references, acceptance, selection, S, resolutions, bootstrap, H, W, F, V, windows, G, C, M, regions, features, zoo, sequence | `{W}/step5/winner_{T}.json`<br>`{W}/step5/rounds_{T}.jsonl` |
| 6 | `step6-hpo.json` | T, fold_table, panel, model, references, acceptance, selection, S, resolutions, bootstrap, H, W, F, V, windows, G, C, M, regions, features, winner, hpo, sequence | `{W}/step6/best_{T}.json`<br>`{W}/step6/rounds_{T}.jsonl` |
| 7 | step 6's template (`step6-hpo.json`), stages from `args.evaluate.stages` | step 6's keys except T and sequence, plus stages | `{L.step6.output}/evaluate/development`<br>`{L.step6.output}/evaluate/later`<br>`{L.step6.output}/report` (shared study directory) |
| report | `report-spec.json` (spec for `dskit.pipeline.workflow_report`) | T, W, report, admission, fold_table, selection_rounds, core, zoo_rounds, hpo_rounds, development, scored, evaluation | `{W}/report/report_{T}/report.md`, `report.html`, `sections/*.csv` |

Loop: step 4 repeats until `selection.stop` holds or `selection.max_rounds`; `selection.group` names the watched output. Study stages and partitions come from the args sequences (`selection.run`, `zoo_sequence`, `hpo.run`, `evaluate.stages`); partition `*` means every declared search partition.

## 5. Variables

Generated from `configs/workflow.json` (`steps.*.in`): a source starting `$args.` is an args path, `$stepN.out.x` or `.pin.x` is an upstream output, `$lane` is the ticker, a bare name is a derivation hook.

| Var | Meaning | Source | Steps |
|---|---|---|---|
| A | as-of date passed to every pipeline run | `$args.A` | 1, 1b, 2, 3 |
| C | chain-proxy settings | `$args.chain_proxy` | 1b, 4, 5, 6, 7 |
| F | feature stores and sources | `$args.feature_sources` | 1b, 4, 5, 6, 7 |
| G | gap, age and range limits; checked against the horizon | `horizon_limits` | 1b, 4, 5, 6, 7 |
| H | horizon, calendar days | `$args.horizon` | 1, 1b, 2, 3, 4, 5, 6, 7 |
| M | market series with max age (own index dropped by policy) | `market_series` | 1b, 4, 5, 6, 7 |
| O | prepared options panel (store reference, keys, date unit) | `$args.options_panel` | 1b |
| S | panel schema: field names | `$args.panel_schema` | 1, 1b, 2, 3, 4, 5, 6, 7 |
| T | ticker (the lane) | `$lane` | 1, 1b, 2, 3, 4, 5, 6, 7, report |
| U | underlying close source: store reference, per-ticker file, columns, fallback rule | `$args.price_source` | 1 |
| V | ticker to its volatility index | `$args.vol_index` | 1b, 4, 5, 6, 7 |
| W | work dir; the ledger workflow.json lives here | `$args.work_dir` | 1, 1b, 2, 3, 4, 5, 6, 7, report |
| acceptance | acceptance bounds | `$args.acceptance` | 4, 5, 6, 7 |
| admission | step 3 admission file | `$step3.out.admission` | report |
| bootstrap | block bootstrap settings | `$args.evaluation.bootstrap` | 4, 5, 6, 7 |
| core | starting core (families + named columns); in the report, the final core from step 4 | `$step4.out.core` | report |
| dates_root | onboarding store of step 2 output | `$step2.out.onboard` | 3 |
| development | step 7 development evaluation | `$step7.out.development` | report |
| evaluation | step 7 report directory | `$step7.out.report` | report |
| families | the one family spec: sources, fields, quality checks, ages | `families_spec` | 1b, 2, 3 |
| features | ordered feature columns (derived from families, core and the lane flag) | `feature_order` | 4, 5, 6, 7 |
| fold_table | fold table with its pin (path, sha256, holdout_start) | `$step3.out.fold_table`, `$step3.pin.fold_table` | 4, 5, 6, 7, report |
| folds | calibration size and roles used from the fold table | `$args.fold_use` | 4, 5, 6, 7 |
| handoff | how a step output is onboarded for the next step (stream names, connector, mode) | `$args.handoff` | 1, 1b, 2, 3 |
| hpo | hyperparameter grid candidates (generated) | `grid_candidates` | 6, 7 |
| hpo_rounds | step 6 rounds | `$step6.out.rounds` | report |
| labels | step 1 chart axis labels | `$args.chart_labels` | 1 |
| panel_root | onboarding store of step 1b output | `$step1b.out.onboard` | 2, 3 |
| references | reference models (feature names resolved to positions) and comparisons | `reference_indices` | 4, 5, 6, 7 |
| regions | decision-region builder settings | `$args.decision_regions` | 4, 5, 6, 7 |
| report | report title, row cap, statements, sections | `$args.report` | report |
| resolutions | grid, sample and audit counts | `$args.evaluation.resolutions` | 4, 5, 6, 7 |
| scored | step 7 scored-folds evaluation | `$step7.out.scored` | report |
| selection | metric, tie rule, stop rule, round cap, pool, group | `$args.selection`, `forward_candidates` | 4, 5, 6, 7 |
| selection_rounds | step 4 rounds | `$step4.out.rounds` | report |
| split | holdout share, family admission rate, fold sizes, warm-up folds | `$args.split` | 3 |
| stream | onboarding stream name of a hand-off | `$args.handoff.streams.dates`, `$args.handoff.streams.panel`, `$args.handoff.streams.target_dates` | 1, 1b, 2 |
| target_root | onboarding store of step 1 output | `$step1.out.onboard` | 1b, 2 |
| windows | return, OHLC and directional windows | `$args.feature_windows` | 1b, 4, 5, 6, 7 |
| zoo | encoder candidates (generated) | `zoo_candidates` | 5 |
| zoo_rounds | step 5 rounds | `$step5.out.rounds` | report |

## 6. Args skeleton

```
{
  "tickers": ["<str>"],
  "A": "<YYYY-MM-DD>",
  "work_dir": "<path>",
  "horizon": "<int>",
  "price_source": {"root": "<path>", "source": "<str>", "stream": "<str>", "relpath": {"<T>": "<str>"}, "columns": {"date": "<str>", "close": "<str>"}, "fallback": "<str>"},
  "chart_labels": {"x": "<str>", "y": "<str>"},
  "options_panel": {"root": "<path>", "source": "<str>", "stream": "<str>", "key_fields": ["<str>"], "ts_unit": "<str>"},
  "feature_sources": {"root": "<path>", "surface|lifecycle|chain_features": {"source": "<str>", "stream": "<str>", "relpath": "<str>"}, "archive_root": {"source": "<str>", "stream": "<str>"}, "price_source": "<str>", "iv_source": "<str>"},
  "vol_index": {"<T>": "<str>"},
  "feature_windows": {"returns": ["<int>"], "ohlc": ["<int>"]},
  "feature_limits": {"fred": {"<name>": {"stream": "<str>", "field": "<str>", "max_age_days": "<int>"}}, "since": "<date>", "max_dte": "<int>", "lags": "<int>", "feature_gap_days": "<int>", "reference_floor": "<float>", "spot_tolerance": "<float>", "surface_features": "<bool>", "matched_dte_vrp": "<bool>", "wing_metrics": "<bool>", "task_features": {"<T>": "<str>"}},
  "limits_contract": {"roles": ["<str>"], "cells": "<str>", "bound": "<str>"},
  "chain_proxy": {"nodes": "<int>", "moneyness_bounds": ["<float>"], "proxy_probabilities": ["<float>"], "...": "<gap limits>"},
  "market_series": {"<name>": {"symbol": "<str>", "max_age_days": "<int>"}},
  "families": {"contracts": {"<family>": {"fields": ["<str>"], "lookback": "<str>", "clock": "<str>", "quality_checks": ["<test>"]}}, "engineered": ["<family>"], "carried": {"<group>": {"fields": ["<str>"]}}, "attach": {"identity": ["<str>"]}, "pending": {}},
  "market_policy": {"drop_own_index": "<bool>"},
  "feature_order": {"slot": "<str>", "names": ["<column>"]},
  "panel_schema": {"symbol|date|quote_date|date_ms|expiry|settlement|settlement_date|selection_group|dte|target|terminal_return|reference|decision_context|entry_close|settle_close|period": "<column>"},
  "split": {"holdout_fraction": "<float>", "tau": "<float>", "folds": {"train_n|val_n|step_n|warmup_folds": "<int>"}},
  "core": {"families": ["<family>"], "names": ["<column>"]},
  "model": {"class": "<module:Class>", "calibrate": "<bool>", "encoder": {"kind": "<str>"}, "losses": [{"kind": "<str>", "weight": "<float>"}], "components": "<int>", "hidden": ["<int>"], "epochs": "<int>", "patience": "<int>", "batch_size": "<int>", "seeds": ["<int>"], "learning_rate": "<float>", "device": "<str>", "deterministic": "<bool>"},
  "zoo": {"candidates": {"<name>": {"encoder": {"kind": "<str>"}}}, "groups": {"<g>": ["<name>"]}, "partitions": {"<p>": ["<name>"]}},
  "zoo_sequence": {"sequence": [{"stage": "<str>", "partition": "<str|*>"}]},
  "references": {"model": "<str>", "comparison": ["<str>"], "models": {"<name>": {"class": "<module:Class>", "params": {"<name param>": "<feature name>"}, "calibrate": "<bool>"}}, "resolve": {"<name param>": "<index param>"}},
  "acceptance": {"blocks": ["<int>"], "min_lower_bound": "<float>", "max_bias_increase": "<float>"},
  "selection": {"metric": "<str>", "max_candidates": "<int>", "max_rounds": "<int>", "stop": {"rule": "<str>"}, "group": "<out name>", "group_name": "<str>", "pool": ["<family>"], "run": {"sequence": [{"stage": "<str>", "partition": "<str|*>"}]}},
  "evaluation": {"resolutions": {"<name>": "<int|list>"}, "bootstrap": {"blocks": ["<int>"], "replicates": "<int>", "seed": "<int>"}},
  "hpo": {"axes": {"hidden": [["<int>"]], "learning_rate": ["<float>"]}, "name": {"pattern": "<str>", "list_join": "<str>"}, "run": {"sequence": [{"stage": "<str>", "partition": "<str|*>"}]}},
  "evaluate": {"stages": {"sequence": [{"stage": "<str>", "partition": "<str>"}]}},
  "decision_regions": {"max_chain_rows": "<int>", "panel_years": ["<int>"], "clock": "<str>", "limits": {"max_seconds": "<int>", "max_resident_mib": "<int>"}, "chain_rule": {"max_abs_log_moneyness": "<float>", "region": "<str>"}},
  "run_env": {"<ENV_NAME>": "<str>"},
  "fold_use": {"cal_n": "<int>", "roles": ["<str>"]},
  "handoff": {"streams": {"<step output>": "<stream name>"}, "connector": "<str>", "mode": "<str>", "source": "<template>"},
  "study_io": {"labels": {"<name>": "<str>"}, "select": {"<key>": "<str>"}, "direction": "<max|min>", "margin": "<float>", "paths": {"<name>": "<str>"}, "naming": {"<name>": "<str>"}, "cli": {"<key>": "<str>"}},
  "report": {"title": "<str>", "lane_field": "<ledger field>", "sections": {"<name>": {"title": "<str>", "format": "<str>", "required": "<bool>", "file": "<path>", "caption": "<str, optional>", "flatten": {"separator": "<str>", "max_items": "<int>"}}}}
}
```
