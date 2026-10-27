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
- Quick pilots (QQQ, minutes each): stack `--args configs/pilot-base.json --args configs/pilot-<x>.json`. Hypotheses and how to read them: `docs/research/distribution-modeling/2026-10-05-performance-pilots.md`.

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


## 7. Proposed Theta source extension (not executable)

[ADR-0269](../../../../docs/architecture/decision-log.md#adr-0269--proposed-theta-source-extension-of-the-existing-price-distribution-workflow) preserves the existing underlying-price-distribution pipeline/model and proposes longer Theta option inputs, five years of initial development and ten sequential ten-week checkpoints with outcome-maturity gaps. The inspected2021-2025 years remain retrospective development. Source mapping, a concrete finite fit budget and fresh reviews precede training; H0-H7 remains unchanged and2026 sealed. Owner selected existing option-feature selection; the latest42-feature baseline stays as the control. A reviewed Theta-to-exact-expiry schema mapping is still required.


## 8. MIO JSON pipeline design (2026-10-27)

**Design only; the proposed node wrappers do not exist yet.** Start with
[mio-json/study.json](mio-json/study.json): supply the externally selected 2019
ticker/DTE rows, source pins, one packet and a fixed variant cohort. Exact DTE
uses equal bounds; bands are inclusive calendar days at the signal date.
The selection rule stays external and unset.

- [Plan](mio-json/plan.template.json): metadata and input readiness; no outcomes.
- [Run](mio-json/run.template.json): freeze and claim one packet, read bounded
  inputs, run fixed variants, replay and publish.
- [Compare](mio-json/compare.template.json): verify and compare the complete
  cohort; no automatic winner or release of the next packet.

Use existing Workflow.expand with study=the study.json object and an output_root.
The run template additionally takes frozen_plan and claim_request; comparison
takes result_receipts. These are future content-bound artifacts, not current CLI
arguments. Templates follow PipelineDocument shape, but plan/run cannot resolve
their proposed uses. Do not invoke them as executable pipelines.

The proposed sequence is exposed 2020 COVID development, two ten-week packets in
2021, two in 2022, then frozen 2023 confirmation. Only one packet opens per
invocation. Brainstorm between packets and freeze changes before opening the
next. DTE-dependent settlement tails finish before the next entry window; longer
DTEs require a valid replan, never silent truncation. Dates are proposals, not
new authorization to read data. Unscheduled weeks stay closed unless separately
recorded as history/tail exposure. All 2020 was already inspected.

History supplies earlier prices only. Each method/packet starts an independent
account; entries, fills and deposits stop at the entry-window end, and the tail
only marks/settles existing positions. Do not concatenate reset account equity
curves. Pair rows apply before projection/optimization and again at replay;
no cross-product of tickers and DTEs.

Owner correction 2026-10-27: A0008 uses the best complete model-zoo output.
The structured predictive selection and producer pins must be bound and agree
with the forecast source before execution; the historical M51 recipe and its
82-origin monitor are not defaults. A0003 and the yearly cadence remain binding.
2019 is predictive warmup, used here only for external MIO eligibility.
The recorded M51 dependency used fallback in 2019 and selection on 2020-23.
Thus later packets support MIO-only comparisons. 2024-25 needs the separate
single-opening holdout protocol; 2026 remains permanently closed.

Current selection binding is specified in study.json's protocol.predictive_binding.
It is a proposed contract: the wrapper implementation and runtime refusal tests
remain future work. The records below retain the original design review; the
publication amendment and current review locks are in ADR-0278.

### Scope and verification record

Base 5747f179; ADR-0278 amendment 'staged MIO JSON design'. Owner narrowed the
current task to JSON design. Four new JSON files, existing documentation
pointers and manifest only; no runtime changes or market reads. Proposed generic
protocol work belongs in dskit; the option adapter reuses the existing weekly
study, portfolio solver and funded ledger. study.json lists every missing seam.

Matrix: sparse/exact/band pairs; pinned source/forecast identity; chronological
history/entry/tail bounds; fill/deposit cutoffs; failed-run exposure and retry;
independent accounts; complete-cohort reporting; protected years; template
expansion/ref wiring; declared runtime gaps. JSON-only checks cover syntax,
substitution, reference wiring, default-date arithmetic, existing solver params
and the child manifest/instruction parity. Future runtime cases are enumerated
in study.json and are not claimed tested by this design packet.

Static checks (WSL2; /home/russell/dskit/.venv/bin/python -B): all four JSON
documents parse without duplicate keys or nonfinite numbers; three templates
expand and pass PipelineDocument grammar with upstream references intact.
Predictive folds equal the existing JSONL; solver/projection params validate.
For max DTE 60, tail ends are 2020-08-07, 2021-05-21, 2021-11-19,
2022-05-20, 2022-11-18 and 2023-12-29 (exclusive), with no packet overlap.
The four-file manifest addition required updating its pinned count 773 -> 777.

Focused manifest/parity and documentation-budget checks: 5 passed.

### Independent design review

Candidate 6ceb2516c6828fb6e9e2aaeede68cb16cb6dccc5; reviewer
/root/mio_design_correctness (GPT-6), correctness/authority lens.
Verdict: 0 Critical, 1 Major, 0 Minor, 0 Nit. MIO-C1: the written checks allowed
2022 to be opened, followed by new variants on unopened 2021, because predecessor
IDs did not define a state transition. This invalidated the temporal boundary;
that candidate stopped before a second lens. Reviewer inspected all four
documents, Path and current study/fill/replay/account seams; independently
confirmed folds and all tail dates; no market reads or runtime tests.

Correction: protocol.release now defines next-frontier-only fresh claims,
predecessor review/retirement, imported-2020 handling, failed-run retry or
abandonment, explicit skip forfeiture, inheritance across revisions/source
aliases, and atomic registry-revision checks. New-cohort freeze follows prior
review; plan/open/compare apply the same rule and receipts. This is a contract
correction, so both independent lenses restart on the next candidate.

### Closed design candidate

Candidate 8c6fc522e23811dd959e17c7dce3dfa62ca54e84; base 5747f179.
Both fresh independent lenses below reviewed that same immutable candidate.
Zero unresolved Critical/Major/Minor/Nit; MIO-C1 closed. Final additions are
evidence only: all four JSON SHA-256 digests remain unchanged. No runtime
implementation, market access, backtest, merge or push was performed.

Retained final report, /root/mio_design_correctness_v2 (GPT-6):

> PASS - design correctness/authority review. Candidate
> 8c6fc522e23811dd959e17c7dce3dfa62ca54e84; unresolved:
> Critical 0, Major 0, Minor 0, Nit 0.
>
> MIO-C1 resolved: shared chronological frontier, predecessor review/retirement,
> imported 2020 handling, failed-run disposition, revision/alias inheritance,
> and atomic claims now agree across plan/open/compare.
>
> Reviewed sparse ticker/DTE admission, external 2019 evidence, predictive
> folds/pins, history/entry/tail boundaries, funding/fills, frozen cohorts,
> reporting, protected dates and declared runtime gaps. No additional proven
> design defect.
>
> Independently checked all four JSONs for duplicate keys/nonfinite values;
> expanded all three templates through Workflow.expand and
> PipelineDocument.from_obj; checked upstream references, exact fold agreement
> and all six default tail dates.
>
> Inspected current study, solver, fill and funded-ledger seams. Their
> limitations are disclosed; proposed wrappers are explicitly non-executable.
>
> Limits: no market data/results reads, runtime enforcement tests, solves or
> implementation-readiness claim. No edits; HEAD and working tree remained
> unchanged.

Retained final report, /root/mio_design_integration (GPT-6):

> PASS - zero unresolved Critical/Major findings.
>
> Reviewer: /root/mio_design_integration (GPT-6), independent
> test-quality/integration lens. Candidate:
> 8c6fc522e23811dd959e17c7dce3dfa62ca54e84; base: 5747f179.
> Final checkout identity matched; worktree clean.
>
> Findings: Critical 0, Major 0, Minor 0, Nit 0.
>
> Reviewed all four JSON documents, README section 8, ADR amendment, locked
> Path, changed-file manifest, and relevant workflow/library interfaces.
> Checked:
>
> - Sparse exact/band ticker-DTE rows; external 2019 selection; empty inputs
>   cannot imply unrestricted eligibility.
> - Exact predictive folds, fallback disclosures, chronological
>   history/entry/tail boundaries, account resets, deposit/fill cutoffs, and
>   protected dates.
> - Fixed method cohorts, complete reporting, shared solver policy, and
>   group_screen: none.
> - Template bindings, upstream references, proposed class ownership,
>   unresolved pins, and explicit runtime gaps.
> - Release correction covering predecessors, review receipts, retirement,
>   failure, retry, revisions, aliases, and inherited read frontier.
>
> Executed in WSL2:
>
> - Focused manifest/parity plus documentation-budget checks: 5 passed.
> - Strict parsing of four JSON files: no duplicate keys/nonfinite values.
> - Three template expansions and PipelineDocument grammar/reference checks:
>   passed.
> - Fold equality and existing solver/projection parameter validation: passed.
> - Default 60-DTE tail calculations: matched all six documented dates; no
>   packet overlap or protected-boundary violation.
>
> Limits: These checks establish design consistency and JSON grammar only.
> Proposed wrappers, atomic claims, bounded readers, replay, holiday sessions,
> failure recovery, and reporting remain unimplemented and untested at runtime.
> No market results were inspected; no solve, backtest, or full suite ran.

Verified JSON file SHA-256 values:

- compare.template.json: c7e458be7745ff94a06a2d26b1eeba6d9eb9026768ea083f1ee28e377fcb0351
- plan.template.json: 805f7c6982cae062ace0cb674debdbc54256ed8f4e10fb1efa75d3a15aed3404
- run.template.json: 97d2686b4913975b7e87ed7ff530611bc0f750893f17160d6450cb1149c732e7
- study.json: e5ba3ed0f7cd14baccf9108573b397385642bafa7f5de021189d0ad15894b4aa
