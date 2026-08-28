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
- **Identity**: sha256 over canonical JSON with every `notes` stripped and the
  top-level `env` / `outputs` / `schedule` sections excluded.
- **Roles** are declared BY the node class, never by the config:
  `data, labels, transform, tensor, accrual, gate, search, signal, train,
  score, stat_test, capital, report`. The planner attaches rules to roles
  (data nodes take no inputs, `capital` requires a `stat_test` upstream,
  `search` objectives must read `val`, …).
- A `clock` section is parseable but refuses to run — declared design,
  not yet executable.

## What ships

Verbs: `run`, `walkforward`, `plan`, `validate`, `nodemap` — and **`runs`**,
the cross-run view (`runs.py`, tier-1 stdlib, no tracking server needed):

```bash
python -m dskit.pipeline runs [--root DIR] [--metric NAME]... [--param PATH]... [--limit N]
```

It scans a run root, reads each run's **structured records** — `result.json`
for name/asof/state/hashes, `nodes/NN-*.json` + `carry.json` for metrics
(`<node>.<metric>`), `config.json` for `--param` columns — and prints one
markdown row per run, newest first. `report.md` is never read: prose is not a
data source. Foreign or partial directories (a walk-forward summary dir, a
half-written run) are listed as skipped with a reason rather than dropped, and
`--limit` counts what it did not show.

Anything the table cannot show, it **names** in a `notes` block under the
table: a metric that reached disk as text rather than as a number (a diverged
`inf`, a `metrics` dict too big to carry), a node record that would not parse,
a missing `nodes/`, an unreadable `config.json`. An empty cell therefore means
one thing only — that run did not measure (or declare) it — which holds for
`--metric` and `--param` alike: a metric key **no** scanned run ever reported,
or a param path **no** scanned run's config declares, is refused outright (a
typo would otherwise render a confident column of blanks), while a key only
some runs measured — or a knob only some documents declare — renders honestly
with blanks for the rest.

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
| `validate` † | score | model-vs-baseline per-record loss on a declared split |
| `stat_test` † | stat_test | per-instrument cluster bootstrap (`method`: plain \| studentized bootstrap-t) + family correction; weighted corrections take a `weights` input |
| `run-report` † | report | renders stage evidence to `evidence.json`/`.md` |

† **owned**: documents may not substitute these kinds with their own class —
the toolkit's statistics are not swappable by config.

`libs/` packs register nothing by import; use their kinds via
`register()`/`--adapter` or reference classes by import path:
**sklearn** `sklearn-fit`/`sklearn-predict` (the document names the estimator);
**torch** `torch-train`/`torch-predict` (DECLARED, ADR-0025: the document
names the `nn.Module` class — no subclass, validated at plan time) +
`torch-linear-train`/`torch-linear-predict` + `TorchTrain`/`TorchPredict`
bases (`build_module` hook; optional `monitor` selects the checkpoint —
the best epoch's weights restore before persist/serve, ADR-0035); **sb3** `sb3-train`/`sb3-policy`/`sb3-eval`
(ADR-0028: the document names the RL algorithm AND the gymnasium env class;
artifacts are hash-pinned); **matplotlib** `mpl-figure` + `FigureNode` base
(ADR-0029: declared line/scatter/bar/hist marks over a row stream → a PNG
artifact); **transformers** `transformers-fit` (declared)
+ `transformers-tiny-fit`/`transformers-predict`; **optuna** `optuna-search`
(categorical spaces; continuous specs are planner-refused, documented);
**pyomo** `pyomo-budgeted-select` + `PyomoSolve` base (`build_model`/`extract`
hooks); **numpy** registers no kinds — subclass `ArrayMap`/`ArrayFeatures` and
wire by import path. Heavy imports live inside `run()` — the tier rule.

Synthetic nodes (`synthetic_nodes.py`) mirror every role for demos and tests;
they register only into private registries, never the default one.

## Writing your own node

```python
from dskit.pipeline.node import Node, register_node_kind

class MyModel(Node):
    role = "train"                     # from ROLES; the class declares it
    outputs = ("signal", "artifact")   # run() must return exactly these

    @classmethod
    def validate_params(cls, params):  # default-DENY: name your knobs, refuse the rest
        ...

    def run(self, ctx, inputs):
        import torch                   # heavy imports go INSIDE run()
        ...

register_node_kind("my-model", MyModel)   # at your package's import
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
├── document.py        PipelineDocument / NodeSpec / ROLES / splits + walkforward specs / refs
├── node.py            Node ABC, NodeContext, NodeKindRegistry, register_node_kind
├── planner.py         document -> Plan: topo order, role rules, wire checks
├── driver.py          LOAD -> IMPORT -> PLAN -> RESOLVE -> EXECUTE -> RECORD; run dirs;
│                      run_walk_forward (one derived run per fold + summary)
├── runs.py            reads run dirs back: scan_runs / format_runs (the `runs` verb)
├── markdown.py        the ONE pipe-table renderer every table here is built by
├── split_policy.py    split-assignment policies (record / event-open / event-close) + EventBounds
├── kinds_flow.py      filter, derive, concat, join, event-bank, eligibility, banking-report
├── kinds_table.py     table-file, table-write (digest-verified keyed tables)
├── kinds_stats.py     owned validate + stat_test (plain + studentized bootstrap-t, corrections)
├── kinds_search.py    hpo-grid (the ctx.rerun seam)
├── kinds_report.py    owned run-report (evidence.json / evidence.md)
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
│                      pyomo, sb3, matplotlib
├── README.md          this file
└── CLAUDE.md          agent orientation
```

Tests: `python -m pytest tests/pipeline -q` (tier-1 + purity gate),
`tests/pipeline_libs -q` (tier-2 packs, importorskip per library).
