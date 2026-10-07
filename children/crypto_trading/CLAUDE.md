Default answer: outcome first, max 5 lines. Expand only if I ask.

# CLAUDE.md — crypto_trading (a dskit child)

Agent orientation template — see README.md for what the child does.

## The child rules (ADR-0021)

- **Standalone explanations live in `docs/explanations/`.** Put worked,
  self-contained explanations there rather than beside decision records or
  research notes.
- **Durable handoffs live in `docs/memos/`.** Keep implementation outcomes,
  operational evidence, and known caveats there. A memo is not an ADR and is
  not journaled research; the skeleton initializes the folder with `.gitkeep`.
- **Fitted artifacts live in `models/`.** ML weights, optimization solutions,
  serialized transforms that outlive a run directory — gitignored, because
  reproducibility is the run document's identity hash, never a committed
  binary. Run-scoped outputs stay in the run directory.
- **Project plans live in `docs/plans/`.** The child's own phased work plans
  and closeout checklists; results move to `docs/memos/`, never into a plan.
- **Never edit dskit.** A missing capability is either a genuinely
  generic gap — propose an ADR upstream — or domain logic that stays
  here. There is no third option.
- **The domain lives here and in `configs/`.** dskit stays domain-blind;
  behavior is JSON the engines validate, self-documented via `notes`
  (the why, not the what), default-deny everywhere.
- **Tier-3 may import anything** — but keep heavy imports inside
  `run()`/`read()`: documents naming these kinds must PLAN on machines
  without the heavy libraries (the conformance suite enforces it).
- **Position-independent**: no `..` imports, no dskit-repo paths; the
  only coupling is `import dskit`. Graduation is a directory move.
- **Import = registration**: `crypto_trading/__init__.py` imports `nodes`,
  which registers the kinds. `--adapter crypto_trading` is exactly this
  import. **Never pass `owned=True`** — that flag RESERVES a kind name so
  no document can point it at a different class, and the toolkit uses it
  only for kinds whose statistics must not be config-swappable
  (`validate`, `stat_test`, `run-report`). A child claiming it would be
  locking down a name it does not own.
- **A vendor knob is a `spec()` knob** — bar interval, feed, universe,
  granularity. If a second project would want it different, it cannot be
  a literal inside `_fetch`.
- **A serving/live loop READS the configs, never restates them.**
  `<run-dir>/config.json` is the whole training document (the driver
  writes it), so lookback, gap discipline, and trainer node keys are
  already on disk; vendor knobs come from the source config. Only
  operational flags (qty, dry-run, log dir) belong on the CLI. Restating
  any of it drifts from the backtest — and a third config file duplicates
  both, so do not add one.
- **"One model per key" needs the grammar, not more JSON.** Hand-expanding
  N filter/train/score nodes per key is the interim; the generic `foreach`
  gap is tracked in dskit's `TODO.md`.
- **Extend a seam; never branch beside it.** A new `if kind ==` chain in a
  `run()` is the smell — that is a subclass, a registry entry, or a
  strategy object. Subclass the toolkit's doorways (`PyomoSolve`,
  `ArrayFeatures`, `TorchAdapter`, `Connector`) and supply only your
  domain. Import `reject_unknown_params` / `check_int_param` from
  `dskit.pipeline.node` rather than copying them.
- **A default belongs to ONE name.** Writing `params.get(k, <literal>)` in
  both `validate_params` and `run` is the most common defect in a node —
  validation approves a value the run never uses, silently. Name it once
  as a module constant.
- **If a value must appear twice, PIN it** with a test or a runtime
  refusal. `test_lookback_agrees_everywhere` plus a module that refuses a
  width mismatch is the shape to copy. And when you add a knob, add it to
  the pinning tuple — a pin that omits a knob claims coverage it lacks.
  (Deliberate restatement in a validation suite or a test is the
  exception, and is correct: an assertion that reads its expectation from
  its subject asserts nothing.)
- **Decisioning is a journal (ADR-0056).** `journal.json` is the
  walk-up marker. Actions (acquire / research / execute / production)
  append `docs/decisioning/actions.csv`; README is generated. Path to
  production is owner `python -m dskit.journal promote` only. Pipeline
  runs and onboarding verbs record themselves. Research always goes
  through `python -m dskit.journal research --topic T --name N`
  (never Write `docs/research/` by hand; no markdown in that root —
  only `docs/research/<topic>/<YYYY-MM-DD>-<name>.md`, with
  `<date>-synthesis.md` as the task summary). Wrap `live.main` in
  `dskit.journal.hooks.production`. An uninitialized child refuses.
- **Path is human-owner-only.** Never add, edit, or regenerate a Path row or
  its `Current Work` field. The owner alone maintains it. Every Path row must
  include an ID, a short label, purpose, relevant files (pipeline run,
  research markdown, or other material evidence), and `LOCKED` as `Y` or `N`.
- The skeleton's file list is pinned in dskit's
  `tests/children/test_skeleton.py` — reshaping the SKELETON means
  updating that pin in the same commit (copies are unpinned).

## Serving a run forward

`dskit.production` owns the loop; a child owns the venue. `nodes.py` answers
`serving_effect` (the source is `entry_read`, the transform is `pure`; the
default is `forbidden`, so silence keeps a class out of a served graph) and
publishes a `serving_contract` with no universe in it — the required key set
is the serve document's, pinned into the release.

`configs/serve-sample.json` serves `run-sample.json` at the **shadow** rung:
it decides for real and sends nothing. `execution.py`, `accounting.py`,
`approvals.py` and `coordination.py` are fail-closed templates; implement one
only when its integration is real, and prove the executor with
`executor_conformance_suite`. `tests/test_production.py` fails the moment a
template becomes convenient enough to send an order.

## Stage A data pulls

Sources are dskit packs (`kalshi`, `httpblobs`, and by import path `kalshi_history`, `restwindow`) plus configs, and no child code: the Binance
daily zips become parquet through dskit's `ZipCsvToParquet` (ADR-0242), the column layout being each
source's `transform_params` (a layout is config; it takes no `notes`, because the block feeds the
declaration digest). A snapshot holds ONE stream, so each stream has its own suite. Series and date lists repeat across configs and suites by design (a
suite restates its vocabulary); `tests/test_configs.py` pins every repeat, so change
both together. The 15-minute candles are one source per series. `kalshi_history` (ADR-0239) adds the
settlement value (`expiration_value`, a LABEL known only from `settlement_ts`: gate on it, never a feature), history
before the live cutoff, event-level hourly candles and trades; `restwindow` (ADR-0240) adds Coinbase (the live-safe spot
alternative to Binance, which is research-only; no node reads it yet) and Deribit DVOL. Their hourly candle and trade
pulls cost a request chain per ARCHIVED market and the pack has no date bound: count first (runbook 7c); a bound is a
new dskit knob, never child code. The digest check is child-side by ruling (ADR-0241 withdrawn). `AGENTS.md` mirrors this file
(a test pins it): edit both. Commands: `docs/plans/2026-10-06-wsl-data-pull-runbook.md`.

## Stage B features and the kill test

`configs/run-features-15m.json` is the document: readers (`kalshi_rows`), decision rows, point-in-time
features, fair values, fees, the kill test, then `records-write-run`. `configs/run-features-hourly.json` is the SAME
nodes over the hourly ladders (a second `MarketRows` reads the 15-minute series only to anchor the index units); every
modelling knob and the held-out cut are shared and pinned equal in `tests/test_configs.py`, so edit both or neither.
Its rows carry `settle_value` and `settlement_ms` (labels): `tests/test_hourly_pipeline.py` proves by a second world
that changing the value, a later bar, a later candle or a later strike moves no earlier decision's feature. Rules
that bind any edit:

- **One information instant, then a trade after it.** `decision_ms` is I: spot = the Binance bar that closed
  before I, quote = the candle that ended by I (the same minute), BVOL strictly before I, a strike anchor
  once known. Fills happen at I + `exec_lag_s` (> 0, pinned) and `tau_s` runs from there. A quote older than
  the spot fakes an edge: `tests/test_zero_edge.py` runs a zero-edge world through the real nodes and a stale
  control that must fail. The spike-planting tests (`test_spot_features.py`, `test_market_state.py`) keep both
  halves, spike and control: keep them when you touch a reader or estimator.
- **Strikes are index dollars, Binance is not.** A 15-minute strike is the previous window's settlement
  value (the BRTI 60 s average), published after the open (`strike_known_lag_s`); Binance runs a few bp
  higher. `StrikeAnchors` + `SpotFeatures` measure the basis at each anchor (strictly before the decision,
  age-capped) and price `spot_brti`; never feed `BinaryFairValue` the raw `spot`. An anchor whose basis is outside
  `basis_range`, or a strike beyond `max_abs_log_moneyness` of `spot_brti`, is a unit slip: skipped or unpriced, never priced.
  Decisions are made only strictly after the strike is known.
- **Units.** Row times are epoch ms; only the candle `ts` from the pack is seconds (`CandleRows` converts and
  refuses an ms value); every vol column is log-return std per sqrt(second). `fields.py` owns shared names.
- **One published table per run.** The `write` node is dskit's `RecordsWriteRun` (`records-write-run`): `{run}` in its path becomes the run
  directory's name and rows carry `run_id`. `localtables` is incremental, so a changed table under an old
  stream name returns `snapshot: null` and serves the OLD rows; never publish under a fixed stream name.
- **No imputation.** Missing is `None` plus a `*_missing` / `*_status` column. A reading older than its age
  cap is missing. Excluded markets and decision rows are listed on ports and censuses, never dropped quietly.
- **Vocabularies are params** (`payoff_by_strike_type`, `result_labels`, `fee_types`, leads, vol
  specs, kill-test segments in epoch ms and margin): a new geometry or series is JSON; `tests/test_configs.py` pins each
  to the stage A suites and sources it must agree with.
- **The generic parts are dskit's, named by import path in the document**: `libs.parquet_series` (day files,
  `StreamManifests`, ADR-0243), `libs.vol_estimators` (variance; `spot` takes the root and sets the column prefix,
  ADR-0244), `fee_mechanics` (ADR-0245), `binary_pricing` + `binary_scoring` (ADR-0246), `kinds_run_write` (ADR-0247).
  The child keeps only what is Kalshi's or the row vocabulary: the `fees` node's `fee_types` (which fee type means which
  mechanic and base rate), `spot_features`, the readers. No venue name belongs in dskit; no generic rule is copied
  back here. Parsing and the cluster error are dskit's too (`dskit.production.base.parse_utc_ms`,
  `dskit.pipeline.stats.cluster_bootstrap_t`): do not re-implement. `tests/test_migration_golden.py` pins the
  migration: every pre-migration row, column and score reproduces bit for bit.
- **The held-out cut is decided before reading results** and never moved afterward (runbook B5). Segments
  cut on the market's close in the 15-minute document and on its settlement instant (`settlement_ms`) in the hourly
  one, whose labels are known later; the documents report `development` only, and reading `heldout` is a recorded
  edit of `report_segments`. Errors cluster on day blocks (`cluster_block_s`), per-event errors beside them.
- Stage B nodes are referenced by import path, not registered, and are research-only (Binance CC BY-NC-SA).
  Nodes that read files themselves take dskit's `StreamManifests` as an input so the data is in the run identity.
  Commands: runbook stage B.

## Layout

```
crypto_trading/           # tier-3 code: connectors.py, nodes.py, and the four
                       #   production seams — execution / accounting /
                       #   approvals / coordination, all fail-closed;
                       #   stage B: fields, clock, kalshi_rows, decisions, anchors,
                       #   ports, spot_features, market_state, fees (the Kalshi
                       #   mapping over dskit's fee mechanics; see the section above)
configs/               # asset-model / source-sample / suite-sample /
                       #   run-sample / serve-sample, plus stage A:
                       #   source-kalshi-crypto[-candles-*|-books-*] /
                       #   source-binance-* (zip layouts in transform_params) /
                       #   binance_vision_dates / suite-kalshi-crypto-* /
                       #   suite-binance-files; history and restwindow:
                       #   source-kalshi-history-* / source-coinbase-* /
                       #   source-deribit-* with a suite each (suite-kalshi-history-*,
                       #   suite-coinbase-candles, suite-deribit-dvol);
                       #   stage B: run-features-{15m,hourly} /
                       #   source-features-{15m,hourly}
models/                # fitted ML/optimization artifacts (gitignored)
journal.json           # dskit.journal marker
docs/decisioning/      # actions.csv + path.csv; README generated
docs/explanations/     # README points to record-explanation
docs/memos/            # README points to memo
docs/plans/            # the child's own plan builds (the WSL runbook: stage A pulls, stage B run)
docs/research/         # topic folders; <date>-synthesis.md + dated notes
tests/                 # conftest bootstrap + configs/connectors/nodes/
                       #   execution/production tests, plus binance_vision
                       #   (the two layouts) and kalshi_crypto (offline,
                       #   scripted transports), test_history_sources and
                       #   test_restwindow_sources (the new packs, scripted),
                       #   test_runbook (every runbook command is real);
                       #   stage B: synthetic.py (offline stores), one test
                       #   file per child module, test_features_pipeline,
                       #   test_hourly_pipeline (leak tests by a second world)
                       #   test_migration_golden (golden/: the
                       #   pre-migration outputs of three worlds) and test_guard_pins (each
                       #   edge-of-data and bad-input guard, one case)
pyproject.toml         # dependencies = ["dskit"]; extras `parquet` (pyarrow), `features` (numpy + pyarrow)
```

Keep this tree and README.md's current when files change.