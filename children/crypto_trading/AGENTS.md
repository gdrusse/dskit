Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md — crypto_trading (a dskit child)

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

Sources are existing packs (`kalshi`, `httpblobs`) plus configs; the only code is
`binance_vision.py`, the `httpblobs` transform (one subclass per file layout, no
knobs; interim until PROPOSED ADR-0239). A snapshot holds ONE stream, so each stream has
its own suite. Series and date lists repeat across configs and suites by design (a
suite restates its vocabulary); `tests/test_configs.py` pins every repeat, so change
both together. Candles are the two 15-minute series, one source each; hourly candles,
the settlement value (`expiration_value`, the model's label) and pre-cutoff history wait on
PROPOSED ADR-0236. Never solve ADR-0236 to 0239 child-side. `AGENTS.md` mirrors this file
(a test pins it): edit both. Commands: `docs/plans/2026-10-06-wsl-data-pull-runbook.md`.

## Stage B features and the kill test

`configs/run-features-15m.json` is the one document: readers (`kalshi_rows`), decision rows, point-in-time
features, fair values, fees, the kill test, then `records-write`. Rules that bind any edit:

- **The leak rule is a contract.** A Binance bar is usable only if `close_time_ms < decision_ms`
  (strictly; bars are labelled by START), BVOL only strictly before it, a Kalshi candle only if
  `end_ms <= decision_ms`. `tests/test_spot_features.py` and `test_market_state.py` plant spikes at and after
  the decision and a control just before it; keep both halves when you touch a reader or estimator.
- **Units.** Row times are epoch ms; only the candle `ts` from the pack is seconds (`CandleRows` converts and
  refuses an ms value); every vol column is log-return std per sqrt(second). `fields.py` owns shared names.
- **No imputation.** Missing is `None` plus a `*_missing` / `*_status` column. A reading older than its age
  cap is missing. Excluded markets and decision rows are listed on ports and censuses, never dropped quietly.
- **Vocabularies are params** (`payoff_by_strike_type`, `result_labels`, fee `base_rate_by_type`, leads, vol
  specs, kill-test segments and margin): a new geometry or series is JSON; `tests/test_configs.py` pins each
  to the stage A suites and sources it must agree with.
- **Interim classes** stand in for PROPOSED ADR-0240 (`day_series.py`), 0241 (`vol_estimators.py`) and 0242
  (`fees.py`, a Kalshi-only copy of `pmquant/fees.py`). Do not grow them; delete them when the ADR lands.
- **The held-out cut is decided before reading results** and never moved afterward (runbook B5).
- Stage B nodes are referenced by import path, not registered, and are research-only (Binance CC BY-NC-SA).
  Nodes that read files themselves take `StreamManifests` as an input so the data is in the run identity.
  Commands: runbook stage B.

## Layout

```
crypto_trading/           # tier-3 code: binance_vision.py (httpblobs transform),
                       #   connectors.py, nodes.py, and the four
                       #   production seams — execution / accounting /
                       #   approvals / coordination, all fail-closed;
                       #   stage B: fields, clock, payoffs, kalshi_rows,
                       #   decisions, day_series, vol_estimators,
                       #   spot_features, market_state, fair_value, fees,
                       #   kill_test (see the section above)
configs/               # asset-model / source-sample / suite-sample /
                       #   run-sample / serve-sample, plus stage A:
                       #   source-kalshi-crypto[-candles-*|-books-*] /
                       #   source-binance-* / binance_vision_dates /
                       #   suite-kalshi-crypto-* / suite-binance-files;
                       #   stage B: run-features-15m / source-features-15m
models/                # fitted ML/optimization artifacts (gitignored)
journal.json           # dskit.journal marker
docs/decisioning/      # actions.csv + path.csv; README generated
docs/explanations/     # README points to record-explanation
docs/memos/            # README points to memo
docs/plans/            # the child's own plan builds (the WSL runbook: stage A pulls, stage B run)
docs/research/         # topic folders; <date>-synthesis.md + dated notes
tests/                 # conftest bootstrap + configs/connectors/nodes/
                       #   execution/production tests, plus binance_vision
                       #   and kalshi_crypto (offline, scripted transports);
                       #   stage B: synthetic.py (offline stores) and one
                       #   test file per module plus test_features_pipeline
pyproject.toml         # dependencies = ["dskit"]; extras `parquet` (pyarrow), `features` (numpy + pyarrow)
```

Keep this tree and README.md's current when files change.