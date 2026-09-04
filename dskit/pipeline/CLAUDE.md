Default answer: outcome first, max 5 lines. Expand only if I ask.

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
- **Trainable kinds** — a `train`/`signal` role subclasses
  `TrainableNode` (ADR-0038), the only role a document may give
  `mode`/`artifact`. `run` and `validate_inputs` are TEMPLATE methods:
  implement `run_train`/`run_load` (both abstract) and the
  `validate_common_inputs`/`validate_train_inputs`/`validate_load_inputs`
  hooks; set `default_mode = "load"` for a kind that only ever loads and
  make `run_train` its refusal. `default_mode` is graded against
  `document.MODES` by `node_class_errors`, beside the `role` check — the
  grammar and the dispatch read ONE vocabulary. Never override either
  template method — the conformance bar checks both still resolve to the
  base. Resolving a
  pin is `Node.pinned_artifact` (node-level pin → declared param → wired
  port) plus `Node.pin_port_problems`; they sit on `Node`, not the
  trainable base, because `sb3-eval` (role `score`) needs them too.
- **Library packs** — `libs/<lib>.py`: name the library only inside a
  method (`run()` for node packs); expose a `NODE_KINDS` tuple +
  `register()` — `libs/mlflow.py` keeps `NODE_KINDS` empty and its
  `register()` claims a `SINK_KINDS` entry instead; ship abstract
  bases with a small hook (`build_module`, `build_model`, `apply`) so
  tier-3 code writes the domain, not the plumbing. The DECLARED kinds
  (`torch-train`/`torch-predict`, `transformers-fit`) go further: the
  document names the library class, `library_path_problems` /
  `import_library_class` (base.py) validate it at plan time. The
  PRETRAINED kinds (`transformers-encode`/`-classify`/`-forecast`,
  ADR-0083) never name a hub: they pin an acquired snapshot by manifest
  hash (`root`/`snapshot`/`stream`) and resolve it once per instance
  through the read seam's `verified_payload_dir`, imported at function
  depth like `scan_stream`; `build_model` / `build_tokenizer` /
  `vectors` / `column_names` / `forecast` are their hooks, and a
  Chronos- or TimesFM-shaped forecaster is a subclass supplying them.
- **Reading an acquired stream** — the `observations` kind
  (`libs/observations.py`, ADR-0077) fronts `dskit.onboarding`'s
  `scan_stream`: children subclass `ObservationRows`, narrow `_PARAMS` to
  the knobs their domain decides (`key_fields` is a fact about the stream,
  not a document knob), and override `project()` to turn the deduplicated
  rows into their record envelope. The scan is memoized per INSTANCE, so
  `fingerprint()` at resolve and `run()` at execute see one snapshot;
  `scan_stream` is imported inside the scan, never at module top.
- **Metrics** — `register_metric` (`metrics.py`); `logloss`/`brier` ship.
- **Corrections** — `register_correction` (`stats.py`);
  `bh`/`bonferroni`/`none`/`weighted-bh` ship. `needs_weights` metadata
  gates the stat_test `weights` input port (plan-time mirror in
  `planner.py`; the stage-list grammar refuses weighted corrections).
  The STATISTIC itself (`METHODS`: plain | studentized) is a closed
  tuple by owned-kind doctrine — never registrable.
- **No-information / h*** — `no_information_test` /
  `max_informative_horizon` (`stats.py`, ADR-0057). One time-ordered
  `(y, ŷ)` series vs a mean; Newey–West `lags` is overlap in **steps**.
  Not a `stat_test` `method`. A panel is the caller's to collapse or
  test per unit. `clark_west_series` can feed `cluster_bootstrap_t`
  when the independence unit is a cluster.
- **Split policies** — `register_split_policy` (`split_policy.py`);
  `record` / `event-open` / `event-close` ship. An event policy needs a
  data node implementing `event_bounds()`, and the driver refuses when
  none supplies it — never a silent fall-back to `record`.
- **Stage-list seams** — `register_transform_kind`,
  `register_optimizer_kind` (empty by design), `register_sink_kind`,
  `BackendRegistry` (zero venues ship — the mechanism is the product).
- **Fitted transforms** — subclass `FittedTransform` (`fitted.py`) and
  implement `fit(rows, params) -> state` + `apply_state(state, rows,
  params) -> rows`. The base owns the rest: the `fit_split` selection,
  the JSON sidecar, the restore under `mode="load"`, the purity screen,
  and the metrics. It is `TrainableNode`'s subclass, so `mode` is never
  yours to handle. Two rules bite: `apply_state` must be PURE and
  ROW-INDEPENDENT (the screen re-applies it to one row alone), and the
  `rows` port carries EVERY input row — `fit_split` says what was
  LEARNED from, never what is emitted. Override `state_problems(state)`
  when a knob DESCRIBES the state rather than sitting beside it (the
  scaler's `features` does): under `mode="load"` a document may restate
  what a state is and never misdescribe it, and a knob `apply_state`
  never reads is where train/serve skew hides. Override
  `row_problems(rows)` for the member's own INPUT shape: it is asked on
  both doorways — this node's stream and the second stream an
  `apply-transform` wires its carrier to — so the sibling half cannot
  validate clean and die inside `apply_state`. A knob `validate_params`
  can judge belongs THERE and not in `state_problems`: the scaler's
  duplicate `features` used to fit and write a sidecar, then have the
  same document refuse its own artifact on the load-mode rerun. The
  screen's comparison is nan-equal (`_same`), so a member may mark an
  absence the way the rest of the repo does. The family's BASE knobs are
  UNSEARCHABLE (`planner.unsearchable_space_why`, ADR-0044): a trial
  override is never plan-checked, so a space addressing `fit_split`
  would fit on the split its objective scores. A member's own knob is
  searchable — that is owner flow 2. `fit_split` must name a split the document
  actually CARVES, not merely one in the vocabulary — `fit_split: "cal"`
  under a splits section with no cal band refuses at plan, where it used
  to reach run and refuse naming the ROWS. And the fit rows arrive in
  `order_field` order only so far as they can: a DECLARED `order_field` a
  fit row cannot answer refuses by name, while a merely defaulted one
  under a cluster-keyed cut (which reads no instant at all) leaves them
  in the stream's order.
- **Feature selectors** — subclass `FeatureSelector` (`fitted.py`,
  ADR-0042) and implement ONE hook, `surviving_features(rows, params) ->
  names`. `fit`/`apply_state` are the BASE's and are already written: the
  state is `{candidates, features}`, the projection drops only the
  REJECTED candidates (a row stripped to its features could neither be
  trained on nor cut), and survivors are canonicalized to the order the
  document DECLARED — return them in any order you like. The extra
  `features` output exists because the surviving list cannot be written
  into a document; the model below reads `"$select.features"`. Need more
  than rows (importance off a fitted net)? Declare the port in
  `validate_train_inputs` and read it with `wired(port)` — the hook's
  signature is the family's and does not change. Two packs ship members:
  `sklearn-select` (any selector by import path — `get_support` is the
  whole requirement) and `torch-importance` (input-gradient sensitivity
  over a wired `signal`). ADR-0044 made a member's own knobs searchable,
  so owner flow 2 (a space over `select.n` / `select.selector`) plans
  and runs; the family's three leakage knobs still refuse.
- **One name per shared vocabulary.** `node.class_ref(cls)` is the
  `module:QualName` an artifact sidecar RECORDS and load mode compares —
  three modules used to write that f-string out, and a divergence there
  orphans stored artifacts rather than returning a wrong number.
  `split_policy.SPLIT_NAMES` is the split vocabulary (`document.py`
  re-exports it as the document's), because the score nodes, the sb3
  pack and the straddle report all ask it and `cal` was added to it once
  already. `records.number_ok` is "a non-bool int or a finite float" —
  `price_ok`/`lead_frac_ok` narrow it, the numpy pack lifts cells and
  order keys by it, and `fitted.py` reads instants and features by it; a
  private copy is how one record lifts on one side of a wire and is
  refused on the other. `planner._carved_splits(document)` is which
  split names a document's cuts actually PRODUCE (`cal` only where a
  band is declared), asked by the `score` reader and the
  `fitted_transform` fitter alike.
- **Conformance** — point `conformance_suite(registry=, probes=,
  expected_roles=)` at any pack; probes are behavioural, not optional
  (`require_probes=False` is a written-down decision).

## Gotchas

- **Journal (ADR-0056).** ``run_document`` / ``run_walk_forward``
  function-import ``dskit.journal`` after RECORD. Module-level is
  still illegal. Folds pass ``journal=False``. Pytest is a no-op.
  An uninitialized child refuses.
- **`_Trackers` SWALLOWS every sink exception** (`driver.py:139-153`) so
  telemetry can never kill a run. Deliberate, and never to be "fixed" —
  the consequence is that a misconfigured sink logs nothing and SAYS
  nothing, so a sink must validate LOUDLY where the swallow cannot
  reach: `validate_params` (runs in `SinkConfig.__post_init__`, i.e. at
  plan) and the constructor (`_open_sinks` runs before the node loop).
  `libs/mlflow.py` is the worked example — default-deny knobs plus a
  stdlib reachability probe of the tracking URI, both at plan time; its
  module docstring carries the whole rationale.
- **…but a sink that RAISES at construction kills the run.**
  `run_document` calls `_open_sinks` one line ABOVE the `try` that wraps
  `_resolve_run`, so a `ConfigError` from a sink
  factory aborts the run before a single node executes. That is right
  for a MISconfiguration and wrong for a destination having a bad day,
  and the two look identical from inside the `except`. `libs/mlflow.py`
  splits them by whose fault they are: mlflow missing, a non-active
  experiment or a store family the installed mlflow refuses raise —
  each names something a human can change; a degraded `http`/`https`
  server that passed the TCP probe, and a LOCAL store that is merely
  BUSY (sqlite lock contention, read off the DBAPI's error classes),
  disable the sink and log a warning. "The probe already proved it
  writable" does NOT make a later local failure the document's fault —
  contention is the ordinary cost of a shared store, and treating it as
  misconfiguration kills correctly configured runs. A new sink pack
  must make the same split, and must bound its own remote calls — a
  telemetry destination that HANGS stalls the run just as fatally as
  one that raises, and no swallow catches a hang.
- **A store family owns BOTH its probe and its failure semantics.**
  `libs/mlflow.py` keeps them in one class-level table
  (`MlflowTracker._DESTINATIONS`: scheme -> `(probe, remote)`) read by
  `probe_destination` and `destination_is_remote`. The version that
  advertised only the probe hook, and decided remote-ness from a private
  module table, handed every subclass failure semantics it never chose.
  If you extend a seam, extend all of what the extension decides.
- **`Tracker.close()` carries no status** and the driver calls it from a
  `finally` on every path, so a sink cannot tell a crashed run from a
  clean one — an mlflow run reads `FINISHED` either way. Giving it one
  is core's change (`protocols.py` + both call sites + the `memory`
  sink), i.e. an ADR; a pack must not guess from `sys.exc_info()`. What
  a pack CAN do, and `libs/mlflow.py` does, is create its remote run
  lazily on the first log, so a run refused before execution leaves no
  empty `FINISHED` run behind at all.
- **Tracking config is hash-EXCLUDED, and excluded a second way.**
  `tracking` is in `DOC_NON_IDENTITY_SECTIONS` (and in base's
  `NON_IDENTITY_SECTIONS`) beside `env`/`outputs`/`schedule`: WHERE
  metrics land is placement, and identity grades what a run COMPUTES.
  But `to_obj` emits a `"tracking"` key for EVERY document, and the
  recipe REMOVES an excluded key — so removing it would have moved
  every hash in the repo, sink or no sink, orphaning every run dir and
  stored artifact. Excluded sections named in `NULLED_IDENTITY_SECTIONS`
  are therefore rendered UNDECLARED (`null`) instead of removed, which
  excludes them just as completely and leaves the canonical JSON
  byte-identical — the same reasoning as `walkforward`'s emit-only-when-
  present. Adding a section to an exclusion list AFTER documents exist
  always needs that treatment. Pinned in
  `tests/pipeline_libs/test_mlflow.py::TestHashPlacement` (identity and
  a golden hash), `test_env_outputs.py` and
  `test_driver.py::test_the_run_hash_ignores_the_tracking_section` —
  the driver keeps its own copy of the recipe.
- **The numpy pack registers no kinds** — `ArrayMap`/`ArrayFeatures`
  (and the concrete `ReturnWindows`) subclasses wired by import path only.
- **An overridden numpy accessor NARROWS `_PARAMS`** (ADR-0040). Answer
  `fields()`/`max_gap()`/… from your own vocabulary and you must drop that
  knob with `narrow_params`, or default-deny approves a value the run
  discards. It is a REFUSAL, not a convention:
  `accessor_narrowing_problems` reports through `validate_params`, so the
  class fails to construct — and it is DERIVED from the MRO, never a
  table of knob names, so a knob that gains an accessor (including one
  YOUR subclass invents) is covered the day it exists rather than the day
  someone remembers to list it. The mirror rule: every per-knob check in
  that pack is guarded by `if "<knob>" in cls._PARAMS`, or a narrowing
  subclass would be refused for omitting a knob it does not have.
- **`keep_mask` compacts, and `latest_rows` therefore checks the MASK.**
  Dropping a position makes the survivors adjacent, which is what
  TRAINING wants and what SERVING must not have: a group whose newest
  position was masked out is ABSENT from `latest_rows`, never served the
  newest survivor wearing a one-bar-old stamp. The other half is
  unconditional too: an INCOMPLETE newest row is absent whatever
  `drop_incomplete` says — that knob governs what `run` EMITS, and a
  serving vector half full of warm-up NaNs is not the truth about now.
- **A carried `group` is normalized by the ENVELOPE's rule**
  (`records.cluster_ok`, imported — the `price_ok` precedent). A dict
  record is interchangeable with a `MarketRecord` throughout that pack,
  so a `group` the envelope would refuse rides as absent: a random split
  hashes `"{seed}:{cluster}"`, and passing `5` through would bucket the
  row differently from the envelope's own answer.
- **A row's split identity is `records.cluster_of`, one function.** The
  envelope publishes `cluster` as a PROPERTY, but a feature row carries
  the raw `group`/`contract` it was built from and no `cluster` key at
  all — so a reader that picked one vocabulary silently mis-cut the
  other. `cluster_of` reads `cluster`, then `CLUSTER_FIELD`, then
  `CONTRACT_FIELD`, each held to `cluster_ok`: an unusable id (`""`, an
  int) hashes exactly like a missing one, so it must not become a bucket
  of its own.
- **`keep_mask` drops are COUNTED** (`n_dropped`, and the `array
  features`/`array map` log lines). Compaction destroys the evidence, so
  the count is taken before it — a vendor outage that zeroes prices has
  to be visible somewhere, and "everything is counted and logged" is the
  pack's promise.
- **Base `Node.validate_params` accepts anything.** The deny lives in
  each class (`_PARAMS` + `_reject_unknown`); forget it and typos pass.
- **Owned kinds** (`validate`, `stat_test`, `run-report`): documents
  cannot substitute their class — the statistics are not config-swappable.
- **`$prev` refs are legal inside `params` only**; any other `$`-string
  is refused. `$splits.<field>` reads the materialized split
  (`val_start_ms` appears there ONLY when an embargo is set,
  `cal_start_ms` only when a cal band is declared — ADR-0034,
  `train_start_ms` only when train is bounded — ADR-0050).
- **Trailing splits DO materialize** — from `Node.data_edge()`. Integer
  `train_days` stamps `train_start_ms` (ADR-0050); `"all-prior"` leaves
  train unbounded on the left.
- **`$splits.train_start_ms` appears only when bounded**, same omission
  discipline as `val_start_ms` / `cal_start_ms`.
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
- **HPO inside walk-forward MEASURES the tuning procedure (ADR-0043)** —
  it is NOT nested CV, and no docstring here may imply it is: a fold's
  evaluation window IS its val split, the planner forces every search
  objective onto a `val` score node, and folds refuse a cal band, so
  nothing de-biases a fold's own score. Cost is folds x (one base pass +
  that fold's executed trials + one winner pass) — but the summary's cost
  line prints only what `_search_cost_line` COUNTED off the fold records
  (folds that searched, trials executed, winner passes applied), because
  a fold that halted before the search node, or whose search raised,
  never paid the shape. Shipping is the plain `run`; freezing a winner is
  an EDIT of the document (pin the values, drop the search node) and
  moves its hash by design — there is deliberately no `freeze` knob and
  no `search_mode`, and adding one would be a second way to say it. The
  fold-internal outer band that WOULD de-bias the estimate is deferred,
  not forgotten.
- **The search diagnostic is emitted only when non-empty** — a fold row
  and the aggregate grow a `search` key only when a fold ran a search
  node, and `_walkforward_search_lines` returns `[]` rather than an
  empty section, so an HPO-free summary stays byte-identical
  (`test_walkforward.py::test_an_hpo_free_summary_is_byte_identical`
  restates the whole report independently and pins it). Same omission
  discipline as the optional document fields — for the same reason.
- **A search winner is recorded by PRESENCE, never coerced** —
  `_search_record` copies `best_params`/`best_score` under the run's own
  names (`winner`/`winner_score`) only when the kind emitted the key, so
  "no winner produced" and "a winner of `None`" stay distinguishable; a
  value JSON cannot hold is NAMED in `winner_dropped`, never summarized
  into a printable stand-in the search never chose. It is populated
  BEFORE `apply_winner`, so a winner-flip refusal still reports the
  winner that caused it. `_json_text` is the single JSON-legality rule
  the record and `carry.json` share, and `_SEARCH_WINNER_FIELDS` (read
  back through `_winner_names`) is the single owner of the two spellings
  — a reader that re-spelled `winner` would silently report every fold as
  winner-less. The aggregate row prints the dropped count beside the
  distinct one: without it, "2 folds with a winner, 1 distinct" reads
  exactly like two folds that agreed.
- **Read `document.expanded`, never `document.pipeline`, in the engine**
  (ADR-0039). `pipeline` is what was WRITTEN; `expanded` is what RUNS —
  and with no `foreach` section it IS `pipeline`, the same object, so the
  switch is byte-identical today. `expanded` and `foreach_groups` are
  `init=False` derived fields that `to_obj` NEVER emits, which is what
  makes them provably not hash material (the hash reads `to_obj` alone).
  A new engine site reading `pipeline` would silently skip every
  fanned-out instance.
- **`"$each"` is a reserved token, not a reference** — `is_node_ref`
  excludes it, so every walker composed of that predicate steps over it
  and outside a template it rides through as a literal. Substituted as a
  WHOLE value only; as a params dict KEY it refuses.
- **A `foreach` template may not pin a node-level `artifact`** — which
  refuses `mode: "load"` templates, since the grammar couples the two.
  Rule 3 substitutes into `params` only and whole-value only, so the
  per-instance PATH cannot be spelled at all; a shared pin would bind N
  instances to one stored model while their training half wrote an
  artifact dir each (`Node.artifact_dir` keys on the node key), and only
  the loud half of that asymmetry is visible. The expressible forms are a
  `default_mode = "load"` kind taking its reference from a param or a
  wired port, or longhand nodes. ADR-0039 is silent here; this is the
  reading, and widening it later is additive.
- **A search `space` key is the one node reference spelled without a
  `$`** — an override PATH, `'<node>.<param.path>'` — so the `foreach`
  expansion re-aims it exactly as it re-aims a wire (ADR-0039, "search
  spaces come for free"): at THIS instance inside a template, at EVERY
  instance in a shared node, so N instances take one declaration instead
  of N copies nothing pins. It needs no `__each` opt-in, because a head
  either names a template or does not, where a port's fannability is
  unknowable. The param name is `document.SEARCH_SPACE_PARAM`, imported
  by the planner and the driver; a new params key whose KEYS address
  other nodes must join that rewrite or it will address a template that
  is not a node.
- **A search `space` value's grammar is SPLIT, not the kind's alone** —
  the planner owns the structural rules (key shape, declared params,
  ancestors of the objective, winner-consistency) AND the one shape
  every search kind shares: a LIST value must be non-empty and hold
  JSON scalars (`planner._is_json_scalar` — restated in
  `kinds_search`, the two pinned to agree), and a DICT value must be
  non-empty — `{}` refuses at plan like `[]`. Past that a dict passes
  through untouched: its INTERNALS are the kind's range-spec form.
  `optuna-search` accepts both; `hpo-grid` refuses range dicts and pins
  that refusal itself. Adding a search kind means writing that value
  grammar **within** the list/dict shapes above — a list of objects, or
  an empty value of either shape, is refused by the planner before your
  kind is consulted.
- **A search node's params defer plan-time `validate_params` whenever a
  param carries an unresolved `$`-ref** — the `objective` contract
  normally guarantees one, so a bad range spec usually refuses when the
  node is CONSTRUCTED mid-run, after the upstream nodes executed. The
  deferral is ref-driven, not kind-driven: with no ref anywhere in
  params, the kind's validator DOES run at plan. A guaranteed plan-time
  gate would be a new planner↔kind protocol — ADR first.
- **Spent record streams are released** (ADR-0048). After a node's last
  `$` reader runs, a list of length `>= 256` (or too big to carry) is
  replaced with `_summarize` and the pinned instance is dropped. A
  not-yet-run search still holds its objective's ancestors. `_carryable`
  never `json.dumps` those streams. `flags` is kept. Do not re-derive
  the length floor — `_RELEASE_MIN_LEN` is the one name.
- **An occupied run dir refuses** — reruns need a new asof or name.
- **`runs.py` reads RECORDS, never `report.md`** — a `metrics` dict is
  summarized out of `nodes/NN-*.json` and recovered from `carry.json`;
  the numeric rule is restated by `driver._node_metrics`, the two copies
  pinned in `test_runs.py::TestMetricRulePin` (same for the run root).
- **Two markdown emitters, ONE pipe-escape.** `runs.py` and
  `driver.py` both print tables and their FORMATS are free to differ —
  a table's shape is taste, which is the ruling that let the second
  emitter ship. Escaping is not taste: a raw `|` ends a cell and hands
  the row a phantom column, so `runs._escape_pipe` is the single owner
  and `driver._md_cell` calls it. What each emitter still decides for
  itself is WHICH values need escaping. Pinned in
  `test_runs.py::TestTableRendering`.
- **Every `RunSummary.note` is PRINTED by the verb** — on refusals too,
  and BEFORE the refusal. `--limit` < 1, a `--metric` nothing scanned
  reported, and a `--param` path nothing scanned declares are refused,
  so a blank cell means "did not measure (or declare) it", nothing else.
- **The run-dir layout is named by `runs.RESULT_FILE`…`NODES_DIR`**;
  `dskit/assets/ingest.py` cannot import pipeline, so its copy is pinned
  in `test_runs.py::TestRunDirLayout`. `resolve.py`'s legacy stage-list
  tree and `{data_root}/pipeline_runs` default are SEPARATE knobs.
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
├── document.py        PipelineDocument / NodeSpec / ROLES + MODES / splits + walkforward
│                      + foreach (ADR-0039: stores what was written, derives what runs) / refs
├── node.py            Node + TrainableNode ABCs, NodeContext, registry, register_node_kind
├── planner.py         document -> Plan; role rules live here
├── driver.py          run_document: LOAD..RECORD, $prev, journal hook
│                      (ADR-0056); run_walk_forward (ADR-0027)
├── runs.py            the READER: scan_runs/format_runs over a run root (`runs` verb)
├── predictions.py     every scored validation row -> one parquet per run (ADR-0064)
├── ordering.py        calibration slope + per-timestamp cross-sectional IC,
│                      with the <5-name usability refusal (ADR-0068, `ordering` verb)
├── attempts.py        AttemptRegistry + session-block max_bar + tier-2 seam
│                      (ADR-0069, `bar` verb)
├── split_policy.py    split policies (record/event-open/event-close) + EventBounds
├── kinds_flow.py      filter, event-grid, derive, concat, join — flow verbs
├── kinds_banking.py   event-bank, eligibility, banking-report — the ★BANKING
│                      accrual -> gate -> ledger spine
├── kinds_table.py     table-file, table-write, records-write (+ the FileWrite base, ADR-0085)
├── kinds_stats.py     owned validate + stat_test
├── kinds_search.py    hpo-grid + top-trials (ctx.rerun seam)
├── kinds_report.py    owned run-report
├── fitted.py          FittedTransform family: standardize, apply-transform,
│                      FeatureSelector
├── conformance.py     conformance_suite + NodeProbe
├── synthetic_nodes.py demo/test nodes, private registries only
├── metrics.py         logloss / brier / squared_error / absolute_error / pinball + register_metric
├── trainlog.py        TrainingCurve + probability metrics (declared-model telemetry)
├── stats.py           cluster bootstraps (plain, studentized-t) + correction
│                      registry; no-information vs mean (Clark–West, h*)
├── records.py         MarketRecord + accounting seams
├── protocols.py       structural Protocols
├── env.py             env + redacting Secrets
├── testing.py         SyntheticBackend, MemoryTracker, register_synthetic
├── base.py            stage-list grammar + config_hash + registries
├── runner.py          stage-list Runner (legacy)
├── features.py        stage-list stream transforms
├── io.py, resolve.py  stage-list load/save + resolution
├── registry.py        venue-backend registry (no venues ship)
├── libs/              numpy, sklearn, torch + torch_ts (ADR-0041 zoo),
│                      transformers (+ the pretrained encode/classify/forecast
│                      trio over an acquired snapshot, ADR-0083), optuna,
│                      pyomo, sb3, matplotlib,
│                      mlflow (tracking SINK pack, no nodes),
│                      observations (the `observations` data kind over the onboarding read seam, ADR-0077)
├── README.md          user-facing docs
└── CLAUDE.md          this file
```

Keep both trees (here and in README.md) current when files change.