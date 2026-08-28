# CLAUDE.md — dskit.pipeline

Orientation for an agent working inside this package. Read the package
[README.md](README.md) first for what it does; this file is how to work
on it without breaking its rulings.

## Conventions

- **Errors accumulate.** Validators append to a list; one `ConfigError`
  reports every problem. Never raise on the first.
- **An invalid object can never exist** — validation in `__post_init__`;
  every `from_obj` rejects unknown keys (default-deny).
- **The canonical hash recipe is pinned** (sorted keys, compact, ASCII,
  NaN refused, `notes` stripped at every level). `dskit.assets` asserts
  byte-parity with it — never "improve" the recipe.
- **Two grammars coexist.** The node-map document (`document.py`,
  `driver.py`) is current; the stage-list config (`base.py`,
  `runner.py`) is the predecessor, unchanged while the migration
  completes. Know which one a symbol belongs to before editing.
- **Roles are declared by the class**, never by the config; planner
  rules attach to roles. New rules go in `planner.py`, not in nodes.

## Extension points

- **Node kinds** — subclass `Node`, then `register_node_kind(name, cls)`
  at import, or reference `pkg.module:Class` directly (no registration;
  an import path can never be `owned`). Project-specific kinds belong in
  a child package (`children/README.md`), NEVER here — and the child is
  the WHOLE adapter unit (ADR-0032): `pipeline_<venue>` sibling packages
  are retired; do not reintroduce the pattern in code or prose.
- **Library packs** — `libs/<lib>.py`: name the library only inside
  `run()`; expose a `NODE_KINDS` tuple + `register()`; ship abstract
  bases with a small hook (`build_module`, `build_model`, `apply`) so
  tier-3 code writes the domain, not the plumbing. The DECLARED kinds
  (`torch-train`/`torch-predict`, `transformers-fit`) go further: the
  document names the library class, `library_path_problems` /
  `import_library_class` (base.py) validate it at plan time.
- **Metrics** — `register_metric` (`metrics.py`); `logloss`/`brier` ship.
- **Corrections** — `register_correction` (`stats.py`);
  `bh`/`bonferroni`/`none`/`weighted-bh` ship. `needs_weights` metadata
  gates the stat_test `weights` input port (plan-time mirror in
  `planner.py`; the stage-list grammar refuses weighted corrections).
  The STATISTIC itself (`METHODS`: plain | studentized) is a closed
  tuple by owned-kind doctrine — never registrable.
- **Split policies** — `register_split_policy` (`split_policy.py`);
  `record` / `event-open` / `event-close` ship. An event policy needs a
  data node implementing `event_bounds()`, and the driver refuses when
  none supplies it — never a silent fall-back to `record`.
- **Stage-list seams** — `register_transform_kind`,
  `register_optimizer_kind` (empty by design), `register_sink_kind`,
  `BackendRegistry` (zero venues ship — the mechanism is the product).
- **Conformance** — point `conformance_suite(registry=, probes=,
  expected_roles=)` at any pack; probes are behavioural, not optional
  (`require_probes=False` is a written-down decision).

## Gotchas

- **`SINK_KINDS` is the TRACKING-sink registry**, not "this node writes
  something": `factory(params)` must return a `Tracker` (consumed by
  `driver.py`/`runner.py`). Only the test `memory` sink exists, and only
  after `testing.register_synthetic()`. A file-writer registered there
  breaks the tracking path — file output is a `report`-role node or
  `table-write`.
- **The numpy pack registers no kinds** — `ArrayMap`/`ArrayFeatures`
  subclasses wired by import path only.
- **Base `Node.validate_params` accepts anything.** The deny lives in
  each class (`_PARAMS` + `_reject_unknown`); forget it and typos pass.
- **Owned kinds** (`validate`, `stat_test`, `run-report`): documents
  cannot substitute their class — the statistics are not config-swappable.
- **`$prev` refs are legal inside `params` only**; any other `$`-string
  is refused. `$splits.<field>` reads the materialized split
  (`val_start_ms` appears there ONLY when an embargo is set, and
  `cal_start_ms` only when a cal band is declared — ADR-0034).
- **Trailing splits DO materialize** — from `Node.data_edge()`; only
  `train_days != "all-prior"` refuses. (Older docstrings claiming
  resolve-time refusal are the stale ones.)
- **A `clock` section parses but refuses to run** — declared design.
- **Document identity excludes `env`, `outputs`, AND `schedule`**;
  the stage-list grammar excludes only `env`/`outputs`. The
  `walkforward` section IS identity and is EMITTED ONLY WHEN PRESENT —
  same for `val_start_ms`/`embargo_days`/`cal_start_ms`/`cal_days` on
  splits: an always-emitted null/zero would move every pre-ADR-0027
  document's hash. Keep that omission discipline for any future
  optional field.
- **Walk-forward folds are separate run series** — the driver suffixes
  each derived document's name `-wf-<cutoff>`, so a `$prev` carry binds
  within one fold's history, never across folds.
- **Optuna continuous specs are planner-refused** (categorical only) —
  documented at the top of `libs/optuna.py`.
- **An occupied run dir refuses** — reruns need a new asof or name.
- **`runs.py` reads RECORDS, never `report.md`** — prose is written for
  a human and free to change wording. A node's `metrics` DICT is
  summarized away in `nodes/NN-*.json` and survives only in
  `carry.json`, so the reader overlays the two; `runs.node_metrics`
  restates `driver._node_metrics` (the driver is pre-standard and owes
  its docstring conversion a commit of its own) and the two are pinned
  together by `tests/pipeline/test_runs.py::TestMetricRulePin` — change
  one and change both.
- The purity gate (`tests/pipeline/test_purity.py`) fails on ANY
  module-level import outside stdlib + this package — heavy imports go
  inside `run()`.
- The synthetic `stat_test` is owned in DEMO registries only; never
  register synthetic nodes into `DEFAULT_NODE_KINDS`.

## Contents

```
dskit/pipeline/
├── __init__.py        public surface; auto-registers the default kinds
├── __main__.py        the CLI: python -m dskit.pipeline
├── document.py        PipelineDocument / NodeSpec / ROLES / splits + walkforward / refs
├── node.py            Node ABC, NodeContext, registry, register_node_kind
├── planner.py         document -> Plan; role rules live here
├── driver.py          run_document: LOAD..RECORD, run dirs, $prev carry;
│                      run_walk_forward (ADR-0027)
├── runs.py            the READER: scan_runs/format_runs over a run root (`runs` verb)
├── split_policy.py    split policies (record/event-open/event-close) + EventBounds
├── kinds_flow.py      filter, derive, concat, join, event-bank, eligibility, banking-report
├── kinds_table.py     table-file, table-write
├── kinds_stats.py     owned validate + stat_test
├── kinds_search.py    hpo-grid (ctx.rerun seam)
├── kinds_report.py    owned run-report
├── conformance.py     conformance_suite + NodeProbe
├── synthetic_nodes.py demo/test nodes, private registries only
├── metrics.py         logloss / brier / squared_error / absolute_error + register_metric
├── trainlog.py        TrainingCurve + probability metrics (declared-model telemetry)
├── stats.py           cluster bootstraps (plain, studentized-t) + correction registry
├── records.py         MarketRecord + accounting seams
├── protocols.py       structural Protocols
├── env.py             env + redacting Secrets
├── testing.py         SyntheticBackend, MemoryTracker, register_synthetic
├── base.py            stage-list grammar + config_hash + registries
├── runner.py          stage-list Runner (legacy)
├── features.py        stage-list stream transforms
├── io.py, resolve.py  stage-list load/save + resolution
├── registry.py        venue-backend registry (no venues ship)
├── libs/              numpy, sklearn, torch, transformers, optuna, pyomo,
│                      sb3, matplotlib
├── README.md          user-facing docs
└── CLAUDE.md          this file
```

Keep both trees (here and in README.md) current when files change.
