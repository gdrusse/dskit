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
  a child package (`children/README.md`), NEVER here.
- **Library packs** — `libs/<lib>.py`: name the library only inside
  `run()`; expose a `NODE_KINDS` tuple + `register()`; ship abstract
  bases with a small hook (`build_module`, `build_model`, `apply`) so
  tier-3 code writes the domain, not the plumbing.
- **Metrics** — `register_metric` (`metrics.py`); `logloss`/`brier` ship.
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
  is refused. `$splits.<field>` reads the materialized split.
- **Trailing splits DO materialize** — from `Node.data_edge()`; only
  `train_days != "all-prior"` refuses. (Older docstrings claiming
  resolve-time refusal are the stale ones.)
- **A `clock` section parses but refuses to run** — declared design.
- **Document identity excludes `env`, `outputs`, AND `schedule`**;
  the stage-list grammar excludes only `env`/`outputs`.
- **Optuna continuous specs are planner-refused** (categorical only) —
  documented at the top of `libs/optuna.py`.
- **An occupied run dir refuses** — reruns need a new asof or name.
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
├── document.py        PipelineDocument / NodeSpec / ROLES / splits / refs
├── node.py            Node ABC, NodeContext, registry, register_node_kind
├── planner.py         document -> Plan; role rules live here
├── driver.py          run_document: LOAD..RECORD, run dirs, $prev carry
├── split_policy.py    split policies (record/event-open/event-close) + EventBounds
├── kinds_flow.py      filter, derive, concat, join, event-bank, eligibility, banking-report
├── kinds_table.py     table-file, table-write
├── kinds_stats.py     owned validate + stat_test
├── kinds_search.py    hpo-grid (ctx.rerun seam)
├── kinds_report.py    owned run-report
├── conformance.py     conformance_suite + NodeProbe
├── synthetic_nodes.py demo/test nodes, private registries only
├── metrics.py         logloss / brier + register_metric
├── stats.py           cluster bootstrap + corrections
├── records.py         MarketRecord + accounting seams
├── protocols.py       structural Protocols
├── env.py             env + redacting Secrets
├── testing.py         SyntheticBackend, MemoryTracker, register_synthetic
├── base.py            stage-list grammar + config_hash + registries
├── runner.py          stage-list Runner (legacy)
├── features.py        stage-list stream transforms
├── io.py, resolve.py  stage-list load/save + resolution
├── registry.py        venue-backend registry (no venues ship)
├── libs/              numpy, sklearn, torch, transformers, optuna, pyomo
├── README.md          user-facing docs
└── CLAUDE.md          this file
```

Keep both trees (here and in README.md) current when files change.
