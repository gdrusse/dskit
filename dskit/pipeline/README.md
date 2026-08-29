# dskit.pipeline

The execution engine: **one JSON document declares a whole process** (data →
transform → train → validate → gate → size → report) as a keyed node map, and
**one command runs any such document**. Everything a run does lives in one
hashable document; same hash = same experiment. A run leaves `config.json`,
`plan.json`, `resolved.json`, `result.json`, `carry.json`, a `run.log`,
per-node records under `nodes/`, and a verdict-first `report.md` in one run
directory (`{name}-{asof}-{hash8}`; an occupied directory refuses).

## The 60-second path

```bash
python -m dskit.pipeline nodemap                                  # full demo run, synthetic nodes
python -m dskit.pipeline run  examples/pipeline/nodemap-minimal.json --asof 2026-01-01
python -m dskit.pipeline plan <doc.json>                          # resolved DAG, no execution
python -m dskit.pipeline validate <doc.json>                      # shape + identity hash
python -m dskit.pipeline run <doc.json> --adapter yourpkg         # import = registration
python -m dskit.pipeline walkforward examples/pipeline/walk-forward.json --asof 1973-08-01
python -m dskit.pipeline runs                                     # every run so far, newest first
```

Exit codes: **0** ran · **3** halted at a NO-GO gate (a halt is a result) ·
**1** error. `--adapter MODULE` (repeatable, also on
`walkforward`/`plan`/`validate`) imports your package first so its registered
kinds resolve. Two more verbs — `demo`
(the default) and `synthetic` — drive the legacy stage-list grammar (below).

## The shape of a run

```
  doc.json { name, pipeline{ key: uses|inputs|params }, splits, walkforward }
      |  python -m dskit.pipeline  run | walkforward | plan | validate | ...
      v                            [--adapter pkg -- import IS registration]
 +--------------------------------------------------------------------------+
 | 1 LOAD    2 IMPORT    3 PLAN      4 RESOLVE      5 EXECUTE      6 RECORD |
 | json ->   uses ->     topo order  fingerprints,  plan order,    every    |
 | document  Node class  role rules  splits, hash   search re-runs outcome  |
 +--------------------------------------------------------------------------+
   `--- a refusal in 1-4 writes nothing ----' `-- run dir opens at 4's end:
      |                                       halts and errors land there --'
      v   the planned DAG -- every $node ref (inputs AND params) is an edge:
   [data]->[gate]->[train]->[score]->[search]->[stat_test]->[capital]->[report]
             |                     ^ the objective param ref --
             |                       params make edges too
             +-> a NO-GO verdict from ANY gate- or stat_test-role
                 node halts every DESCENDANT
      |  every node, on a NodeContext: validate_params ->
      v  validate_inputs -> run -> validate_outputs -> nodes/NN-key.json
   {name}-{asof}-{hash8}/ -- its carry.json feeds the next run's "$prev"
```

- **Document + `$`-refs** — `PipelineDocument`, `NodeSpec`, `ROLES`,
  `parse_node_ref`, `parse_prev_ref`, `load_document` (`document.py`).
- **The verbs** — `main` dispatching `cmd_run` / `cmd_walkforward` /
  `cmd_plan` / `cmd_validate` / `cmd_nodemap` / `cmd_synthetic`, falling
  through to `cmd_demo`; `--adapter` is `_import_adapters` (`__main__.py`).
- **IMPORT + PLAN** — `resolve_uses` against `NodeKindRegistry` /
  `DEFAULT_NODE_KINDS`, guarded by `node_class_errors` (`node.py`); each
  `kinds_*.py` `register` claims its names at package import, `libs/` packs
  only via `register()`/`--adapter`. `plan` → `Plan` (`order`, `edges`,
  `role_of`, `ancestors`, `descendants`) and the role rules: `planner.py`.
- **RESOLVE → RECORD** — `run_document` → `DocumentRunResult`
  (`driver.py`; `exit_code` derives from `state`); trailing cuts materialize
  in `_materialize_splits` off `Node.data_edge`, event policies bind in
  `_bind_event_bounds` (both `driver.py`) over `merge_event_bounds`
  (`split_policy.py`).
- **The search seam** — `ctx.rerun` is `_SearchSeam` (`driver.py`, driven by
  `HpoGrid`, `kinds_search.py`), for `search` roles only and never an edge:
  each trial re-executes the dirty part of the objective's ancestry, then
  `apply_winner` runs it once more and that pass replaces those outputs.
  What it chose rides out on `DocumentRunResult.search`, node-keyed
  (ADR-0043).
- **Series and folds** — `_find_prev_run` + `_materialize` bind `$prev`;
  `run_walk_forward` + `_fold_splits` → `WalkForwardRunResult` (`driver.py`).
- **Reading runs back** — `scan_runs` → `RunSummary` / `RunProblem`,
  `format_runs`, `param_at` (`runs.py`), behind the `runs` verb.

## The document

```jsonc
{
  "name": "my-run",                       // ^[a-z0-9][a-z0-9._-]*$
  "notes": "why this run exists",         // allowed at every level, never hashed
  "pipeline": {
    "<key>": {
      "uses": "<kind or pkg.module:Class>",
      "inputs": { "port": "$other_node.output" },   // wiring; order = topo sort
      "params": { },                                // the class's knobs, default-deny
      "mode": "train"                               // trainable roles: train | load
    }
  },
  "splits": { "kind": "time|random|trailing", ... },
  "outputs": { "run_root": "" }           // "" -> ./pipeline_runs
}
```

- **Refs**: `"$node.port"` wires outputs; `"$splits.<field>"` reads the
  materialized split; `{"$prev": "node.output", "default": X}` (params only)
  carries a value from the previous run of the same series.
- **Splits**: `time` (explicit epoch-ms cuts; optional `val_start_ms` opens
  an EMBARGO band — records between `train_end_ms` and it belong to NO
  split, ADR-0027; optional `cal_start_ms` carves a CALIBRATION band out
  of the val window's tail — `split_of` returns a fourth name, `"cal"`,
  for `[cal_start_ms, val_end_ms]`, ADR-0034), `random` (cluster-hashed;
  `train+val == 1.0` is legal — no test split; never yields `"cal"`),
  `trailing` (windows counted backward from the data's edge
  via `Node.data_edge()`; `train_days` must be `"all-prior"`; optional
  `embargo_days` carves the band out of train's tail; optional `cal_days`
  carves the cal band between test and val). Time-based splits
  take an optional **`policy`** — `record` (default) | `event-open` |
  `event-close` — deciding WHICH instant assigns a record to a side, so a
  multi-record event never straddles a cut under an event policy. Event
  policies need a data node supplying `Node.event_bounds()`; the driver
  refuses loudly when none does. The defaults are hash-neutral: pre-policy,
  pre-embargo, pre-cal documents keep their identity.
- **Walk-forward** (ADR-0027): an optional `walkforward` section — fold
  cutoffs (explicit list or `first`/`step_days`/`count`), `val_days`,
  `embargo_days`, an `objective` ref, `select` — and the `walkforward` verb
  runs one derived document per fold, each with its own full run dir:
  splits replaced by that fold's pinned cuts, the declared split `policy`
  riding along (ADR-0031), plus an aggregate summary dir. The section IS
  identity; a fold that halts is a result, a fold that errors stops the
  plan. Folds carry no cal band (ADR-0034 v1) — a parent document
  declaring one refuses pre-flight.
- **Walk-forward + a search node** (ADR-0043): every fold re-tunes on its
  own, so this MEASURES the tuning procedure — rolling-origin performance
  of "search, then fit" — and is never an unbiased estimate of a tuned
  model (nothing de-biases a fold's score: a fold's evaluation window IS
  its val split, which is what the search optimized). **It costs
  folds x (one base pass + the trials that fold executed + one winner
  pass)** — and the summary's cost line COUNTS what the folds paid (folds
  that searched, trials executed, winner passes applied), never what the
  shape predicts they would. The summary prints each fold's winner plus,
  per search node, how many folds chose one, how many DISTINCT ones they
  chose, and how many winners JSON could not hold — that third number is
  what makes the first two add up. Folds may legitimately disagree, and
  that is a number to read, not folklore. **What ships is the plain
  `run`**: freezing a winner means EDITING the document — pin the values,
  drop the search node — which moves its hash by design, because a
  different computation is a different identity. A summary whose folds
  declared no search node is byte-identical to a pre-ADR-0043 one.
- **Fan-out** (ADR-0039): an optional `foreach` section — `keys` (a declared
  list, sorted at construction) plus a `pipeline` of TEMPLATE nodes — expanded
  at document construction, so "one model per symbol" stops being N
  hand-copied nodes. A template `t` becomes `t__<slug>` per key; a reference
  naming a template key rewrites to that key's instance; a `params` value that
  is EXACTLY `"$each"` becomes the key string (whole values only — never
  substring interpolation, and never a params KEY); and a SHARED node fans a
  port out only when it opts in by writing it `<base>__each`. A search
  `space` key naming a template — `"qhat.min_train"` — expands the same way,
  to that instance inside a template and to every instance in a shared search
  node, so tuning N instances is ONE declaration rather than N copies nothing
  pins together. `pipeline` may
  be empty when a `foreach` is declared, and a template may NOT pin a
  node-level `artifact` (so no `mode: "load"` template): the pin names ONE
  stored model and the grammar has no interpolation, so every instance would
  silently restore that same one while the training half wrote a dir each —
  restore through a param or a wired port instead, or spell those nodes
  longhand. No expressions, no conditionals, no
  nesting — this is fan-out, not templating. The section IS identity (adding a
  key is a different computation) while the expansion is DERIVED
  (`document.expanded`, never emitted, never hashed), which is why every
  pre-ADR-0039 hash is unmoved. See `examples/pipeline/foreach-fanout.json`.
- **Identity**: sha256 over canonical JSON with every `notes` stripped and the
  top-level `env` / `outputs` / `schedule` sections excluded.
- **Roles** are declared BY the node class, never by the config:
  `data, labels, transform, tensor, accrual, gate, search, signal, train,
  score, stat_test, capital, report, fitted_transform`. The planner attaches
  rules to roles (data nodes take no inputs, `capital` requires a `stat_test`
  upstream, `search` objectives must read `val`, `fitted_transform` must
  declare the split it fits on, …).
- A `clock` section is parseable but refuses to run — declared design,
  not yet executable.

## What ships

The **`runs`** verb (`runs.py`, tier-1 stdlib, no tracking server) is the
cross-run view:

```bash
python -m dskit.pipeline runs [--root DIR] [--metric NAME]... [--param PATH]... [--limit N]
```

One row per run, newest first, from the **structured records** — never
`report.md`. What the table cannot show it prints beside it: skipped non-run
directories, the `--limit` count, and `notes` naming every measurement
recoverable only as text (a diverged `inf`, a truncated record). A `--metric`
or `--param` **no** scanned run reported (or declares) is refused, never
rendered as a confident column of blanks.

Registered kinds (`DEFAULT_NODE_KINDS`, importing `dskit.pipeline`):

| Kind | Role | Does |
|---|---|---|
| `filter` | transform | keep records passing `where` clauses / instrument list |
| `derive` | transform | add one declared field per record via `when`/`value` cases (no expression language — deliberately) |
| `concat` | transform | merge record streams into one |
| `join` | transform | attach keyed lookup rows to records |
| `table-file` | transform | load a digest-verified keyed table (refuses drift) |
| `table-write` | report | write a table atomically, never clobbering |
| `event-bank` | accrual | count distinct banked events per instrument |
| `eligibility` | gate | admission bar `min_events`; empty family ⇒ NO-GO |
| `banking-report` | report | the banked/in-family/gap ledger |
| `hpo-grid` | search | grid search over `"node.param.path"` via the rerun seam |
| `standardize` | fitted_transform | centre/scale declared features on `fit_split`'s statistics |
| `apply-transform` | transform | project a second stream through a wired fitted carrier |
| `validate` † | score | model-vs-baseline per-record loss on a declared split |
| `stat_test` † | stat_test | per-instrument cluster bootstrap (`method`: plain \| studentized bootstrap-t) + family correction; weighted corrections take a `weights` input |
| `run-report` † | report | renders stage evidence to `evidence.json`/`.md` |

† **owned**: documents may not substitute these kinds with their own class —
the toolkit's statistics are not swappable by config.

**Fitted transforms are a family, not a kind** (`fitted.py`, ADR-0040). A
transform that LEARNS — a scaler's means, a selector's surviving columns —
cannot live with the pure transforms, whose causality guard depends on purity.
So it declares `fit_split`: which split the state is learned from, refused at
plan when a FITTING document declares no splits (a `walkforward` section
counts — folds materialize their own), and checked against the sidecar when a
pinned state is restored. A serving document (`mode: "load"`) fits nothing, so
none of those plan rules apply to it. The `rows` port carries EVERY input row
transformed — applying a train-fit state to val and test IS the required
behaviour; the leak would be FITTING on them. Under a CLUSTER-KEYED cut
(`splits.kind: "random"`, or a time split on an event policy) every row must
carry a USABLE identity — `records.cluster_of` reads `cluster`, then `group`,
then `contract`, the same rule and the same vocabulary the feature rows are
built with — or the fit is refused: an absent or unusable id (`""`, an int)
hashes like every other, so those rows land in one bucket, which is the whole
stream. Under a TIME cut the frame's OTHER half is what is read, so a row whose
declared `order_field` carries something the cuts cannot compare (a string
timestamp) is refused by name rather than dying inside the assignment. A search
`space` may not address the role at all: every knob the base declares re-aims
what the state learned from, and a trial's override is never plan-checked — so
`{"scaler.fit_split": ["train","val","test"]}` would fit a trial on the very
split its objective scores. A member's
own row rule (`row_problems`) is asked on BOTH doorways — its own stream and
the second stream an `apply-transform` wires its carrier to — and `standardize`
refuses a declared feature that NO row of either stream carries, because a typo
is an error, not an identity transform. It refuses one declared TWICE at plan
for the same reason: the state would cover it once, and the load-mode rerun of
that same document would then blame the artifact for a typo in the document.
Write a member by implementing two methods:

```jsonc
"scaler": {
  "uses": "standardize",
  "inputs": { "rows": "$window.records" },
  "params": { "fit_split": "train", "features": ["ret_lag_0", "ret_lag_1"] },
  "notes": "Fit on train only; every row is emitted, scaled by the train state."
}
```

**Feature SELECTION is a member of that family** (`FeatureSelector`, ADR-0042),
so the leakage rules above are inherited rather than restated: fitting sees the
declared split and nothing else. Subclasses implement ONE hook,
`surviving_features(rows, params) -> names`, and the base owns the rest — the
state IS the surviving column list (a JSON sidecar artifact, so serving projects
the identical columns in the identical order), `apply_state` drops only the
REJECTED candidates (the label, the instant and the cluster id are not features
and must ride along), survivors come back in the order the document DECLARED its
candidates (a tie must not order by whatever the library returned), and `metrics`
carries `n_candidates`/`n_selected`. A fourth output, `features`, is what makes
it composable: the surviving list cannot be written into a document — it is the
fit's answer — so the model below reads `"features": "$select.features"`.
A member needing more than rows (importance off a fitted net) declares an input
port and reads it with `wired(port)`.

```jsonc
"select": {
  "uses": "sklearn-select",
  "inputs": { "rows": "$window.records" },
  "params": {
    "fit_split": "train",
    "features": ["ret_lag_0", "ret_lag_1", "spread"],
    "selector": "sklearn.feature_selection.SelectKBest",
    "selector_params": { "k": 2 },
    "score_func": "sklearn.feature_selection.mutual_info_regression",
    "label": "y"
  },
  "notes": "Chosen on train rows only. The model below reads $select.features."
}
```

`libs/` packs register nothing by import; use their kinds via
`register()`/`--adapter` or reference classes by import path:
**sklearn** `sklearn-fit`/`sklearn-predict`/`sklearn-select` (the document names
the estimator, and the SELECTOR the same way — `selector` plus the two arguments
no JSON block can hold, `estimator` for a wrapper selector and `score_func` for a
univariate one, each a dotted path; ADR-0042 —
so a search space over `model.estimator` IS a model sweep, with no per-model
classes: `examples/pipeline/model-sweep.json` plus the pack docstring's
estimator table; `lightgbm.LGBMRegressor` joins via the `lightgbm` extra, not
a pack of its own);
**torch** `torch-train`/`torch-predict` (DECLARED, ADR-0025: the document
names the `nn.Module` class — no subclass, validated at plan time) +
`torch-linear-train`/`torch-linear-predict` + `TorchTrain`/`TorchPredict`
bases (`build_module` hook; optional `monitor` selects the checkpoint —
the best epoch's weights restore before persist/serve, ADR-0035) +
`torch-importance` (feature selection by input-gradient sensitivity: it ranks a
net someone else fitted, wired in on the `signal` port, and trains nothing
itself); **sb3** `sb3-train`/`sb3-policy`/`sb3-eval`
(ADR-0028: the document names the RL algorithm AND the gymnasium env class;
artifacts are hash-pinned); **matplotlib** `mpl-figure` + `FigureNode` base
(ADR-0029: declared line/scatter/bar/hist marks over a row stream → a PNG
artifact); **transformers** `transformers-fit` (declared)
+ `transformers-tiny-fit`/`transformers-predict`; **optuna** `optuna-search`
(categorical lists AND `{"low", "high"[, "log"][, "int"]}` continuous ranges;
`hpo-grid` keeps refusing ranges — enumerating an interval is meaningless);
**pyomo** `pyomo-budgeted-select` + `PyomoSolve` base (`build_model`/`extract`
hooks); **numpy** registers no kinds — subclass `ArrayMap`/`ArrayFeatures`
(or the concrete `ReturnWindows`) and wire by import path. Heavy imports live
inside `run()` — the tier rule.

The numpy pack's lifting is DECLARED (ADR-0040): `group_field`, `order_field`,
`fields` and `max_gap` on both doorways, plus `carry_fields`,
`require_fields` and `drop_incomplete` on `ArrayFeatures`, each read through a
public accessor a subclass may override — and **an override must narrow that
knob out of `_PARAMS`** (`narrow_params`), which the pack refuses at
construction if you forget. That refusal reads the MRO, not a list of knob
names, so it covers knobs your own subclass invents too. `max_gap` splits each
ordered group into
gap-free SEGMENTS before any offset arithmetic, so no lag, lead or return
spans a session boundary; absent, there is one segment per group and the
behaviour is what it always was. `ReturnWindows` composes the vectorized ops
(`lag`, `lead`, `log_return`, `pct_return`) into lags plus a forward label,
and a forward-reading column DECLARES its horizon (`lookahead_columns`)
rather than escaping the causality screen. Lags and the label share ONE
dict, so a `label_name` that IS a lag column `lag_prefix`/`lookback`
produce is refused at plan naming both knobs — a forward value sitting in
a past column is the one leak that screen cannot see, so it is made
unexpressible instead. `latest_rows` is the SERVING call —
the newest row per group with the forward columns dropped — and its "complete,
or absent" rule is UNCONDITIONAL: `drop_incomplete` governs what `run` emits,
never what serving publishes. Positions a `keep_mask` rejects are counted
(`n_dropped`) and logged, so a rule that quietly eats a stream is visible.
The build is whole-column throughout — measured against the per-row Python
chain it replaced, not asserted (`tests/pipeline_libs/test_numpy.py::
TestVectorization`).

**mlflow** is the odd pack out (see its module docstring for the whole
rationale): it ships no node kind at all, because it fills the TRACKING-sink
registry instead. Call `dskit.pipeline.libs.mlflow.register()`
(idempotent — the seam `testing.register_synthetic()` uses for the test
`memory` sink) and a document may then declare where its metrics land:

```jsonc
"tracking": {
  "sinks": [{
    "kind": "mlflow",
    "params": { "tracking_uri": "sqlite:///mlruns.db", "experiment": "my-study" },
    "notes": "Local sqlite store — serverless. Runs land with every node's params flattened to '<node>.<param.path>' keys, so you can filter by hyperparameter."
  }]
}
```

Knobs: `tracking_uri` (default `sqlite:///mlruns.db`; schemes `""`/`file`/
`http`/`https`/`sqlite`), `experiment`, `run_name`, `tags`, `connect_timeout` —
and documentation goes in the sink's own `notes` field, beside `params`, never
inside them. That list, the default and the scheme vocabulary are the THIRD copy
of values the pack owns, so they are pinned to the constants by
`test_the_readme_states_the_knobs_the_default_and_the_vocabulary`: change
`_PARAMS`, `DEFAULT_TRACKING_URI` or `_DESTINATIONS` and change this paragraph.
The default is sqlite and not the older `./mlruns` DIRECTORY for a reason: the
two directory spellings — a bare path and `file:` — reach a store that mlflow
3.x put into maintenance mode and **refuses** unless `MLFLOW_ALLOW_FILE_STORE`
is set in your environment, and this pack never opts you in. They stay in the
vocabulary for mlflow 2.x and for anyone who has opted in; on a modern mlflow
they plan clean and then raise at sink construction, carrying mlflow's own
message and leaving nothing on disk. Prefer `sqlite:` unless you know otherwise.
Another store family (postgres, say) arrives by subclassing `MlflowTracker` and
laying your scheme over `_DESTINATIONS`, which carries both the probe that
proves it reachable and whether it is a server. Skipping `register()` is fine —
spell the class instead: `"kind": "dskit.pipeline.libs.mlflow:MlflowTracker"`,
validated by the same rules. **The sink is loud on purpose**: unknown knobs, an
unreachable destination (a missing store directory, a server that refuses a TCP
connection) and a DELETED experiment all fail before the run, because the driver
deliberately SWALLOWS sink exceptions at run time so telemetry can never kill a
run — an unchecked misconfiguration would log nothing and say nothing. What is
loud but **not** fatal is a destination having a bad day: a server that accepted
the plan-time connection and is now failing, or a local store another process
holds the write lock on. That is the store's condition, not your document's, so
the sink disables itself with a logged warning and your run proceeds.
`connect_timeout` is the budget for both the probe and the sink's own mlflow
calls, so a degraded server cannot stall the run either. Nothing about tracking
touches a document's identity hash — `tracking` is excluded by name, like
`env`/`outputs`/`schedule` — so repointing a study at another store keeps its
run directory and its `$prev` series. Install it with
`pip install "dskit[mlflow]"`.

Synthetic nodes (`synthetic_nodes.py`) mirror every role for demos and tests;
they register only into private registries, never the default one.

## Writing your own node

```python
from dskit.pipeline.node import Node, register_node_kind

class MyTransform(Node):
    role = "transform"                 # from ROLES; the class declares it
    outputs = ("records",)             # run() must return exactly these

    @classmethod
    def validate_params(cls, params):  # default-DENY: name your knobs, refuse the rest
        ...

    def run(self, ctx, inputs):
        import numpy                   # heavy imports go INSIDE run()
        ...

register_node_kind("my-transform", MyTransform)   # at your package's import
```

A **trainable role** — `TRAINABLE_ROLES`, today `train`/`signal`/
`fitted_transform` — subclasses `TrainableNode` instead (ADR-0038): those are
the only roles a document may give `mode`/`artifact`, and the base owns that
dispatch. `run` and `validate_inputs` are template methods:
write `run_train`/`run_load` (both required, so an incomplete trainable refuses
at construction) and, when validation differs by mode,
`validate_common_inputs` / `validate_train_inputs` / `validate_load_inputs`. A
kind that only ever loads sets `default_mode = "load"` and makes `run_train`
its refusal. **Do not override `run` or `validate_inputs`** — the conformance
bar checks both still resolve to the base, because a wrapper is where a second
opinion about `mode` regrows. `Node` itself carries `pinned_artifact`
(node-level pin → declared param → wired port, refusing a contradiction) and
`pin_port_problems`, so a non-trainable role can resolve a pin too.

```python
from dskit.pipeline.node import TrainableNode

class MyModel(TrainableNode):
    role = "train"
    outputs = ("signal", "artifact_path")

    def run_train(self, ctx, inputs): ...
    def run_load(self, ctx, inputs): ...      # restore the pin; NEVER refit
```

Reference it as `"uses": "yourpkg.nodes:MyModel"` (no registration needed) or
by kind name with `--adapter yourpkg`. Then hold your pack to the bar with the
reusable conformance suite — it wants behavioural probes, not just names:

```python
from dskit.pipeline.conformance import NodeProbe, conformance_suite
TestConformance = conformance_suite(
    registry=NODE_KINDS, probes=my_probes, expected_roles=MY_ROLES)
```

(`tests/pipeline/test_toolkit_conformance.py` is the worked example. Passing
`require_probes=False` runs structural checks only — a decision, write it down.)
Project-specific nodes belong in YOUR package — a dskit **child**, which is
the whole adapter unit (ADR-0032: never a `pipeline_<venue>` sibling, in
dskit or beside it; see `children/README.md`) — never here.

## The legacy stage-list grammar

`PipelineConfig`/`Runner` (fixed stages `train → validate → stat_test →
optimize → backtest`) predates the node map and stays unchanged while the
migration completes; `demo` and `synthetic` drive it. Its seams still matter:
`register_transform_kind` (`filter`/`regroup` ship), `register_optimizer_kind`
(none ship), and `register_sink_kind` — the **tracking** seam: a sink factory
returns a `Tracker` (metrics destination); only the test `memory` sink exists,
registered by `testing.register_synthetic()`; real sinks (mlflow, …) register
application-side.

## Contents

```
dskit/pipeline/
├── __init__.py        public surface; auto-registers the default kinds
├── __main__.py        the CLI: python -m dskit.pipeline
├── document.py        PipelineDocument / NodeSpec / ROLES + MODES / splits + walkforward
│                      + foreach specs / refs / the foreach expansion
├── node.py            Node + TrainableNode ABCs, NodeContext, NodeKindRegistry, register_node_kind
├── planner.py         document -> Plan: topo order, role rules, wire checks
├── driver.py          LOAD -> IMPORT -> PLAN -> RESOLVE -> EXECUTE -> RECORD; run dirs;
│                      run_walk_forward (one derived run per fold + summary)
├── runs.py            reads run dirs back: scan_runs / format_runs (the `runs` verb)
├── split_policy.py    split-assignment policies (record / event-open / event-close) + EventBounds
├── kinds_flow.py      filter, derive, concat, join — the record-flow verbs
├── kinds_banking.py   event-bank, eligibility, banking-report — the ★BANKING
│                      accrual -> gate -> ledger spine
├── kinds_table.py     table-file, table-write (digest-verified keyed tables)
├── kinds_stats.py     owned validate + stat_test (plain + studentized bootstrap-t, corrections)
├── kinds_search.py    hpo-grid (the ctx.rerun seam)
├── kinds_report.py    owned run-report (evidence.json / evidence.md)
├── fitted.py          the fitted-transform family: FittedTransform (role
│                      fitted_transform, fit/apply_state hooks, fit_split +
│                      the purity screen), standardize, apply-transform,
│                      FeatureSelector (the surviving-columns member)
├── conformance.py     conformance_suite + NodeProbe — the reusable pack bar
├── synthetic_nodes.py every role, deterministic, for demos/tests
├── metrics.py         logloss / brier / squared_error / absolute_error + register_metric
├── trainlog.py        per-epoch TrainingCurve + probability metrics (logloss/brier/ECE)
├── stats.py           cluster bootstraps (plain, studentized-t); correction
│                      registry (bh / bonferroni / none / weighted-bh) +
│                      register_correction
├── records.py         MarketRecord envelope + binary / mark-to-market accounting
├── protocols.py       structural Protocols (DataSource, Tracker, ...)
├── env.py             env file + redacting Secrets façade
├── testing.py         SyntheticBackend, MemoryTracker, register_synthetic
├── base.py            stage-list grammar: PipelineConfig, registries, config_hash
├── runner.py          stage-list Runner (legacy)
├── features.py        stage-list stream transforms (filter / regroup)
├── io.py, resolve.py  stage-list load/save + resolution
├── registry.py        venue-backend registry mechanism (no venues ship)
├── libs/              tier-2 packs: numpy, sklearn, torch, transformers, optuna,
│                      pyomo, sb3, matplotlib, mlflow (the tracking SINK pack —
│                      registers into SINK_KINDS, no node kinds)
├── README.md          this file
└── CLAUDE.md          agent orientation
```

Tests: `python -m pytest tests/pipeline -q` (tier-1 + purity gate),
`tests/pipeline_libs -q` (tier-2 packs, importorskip per library).
