Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md — yourproject (a dskit child)

Agent orientation template — see README.md for what the child does.

## The child rules (ADR-0021)

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
- **Import = registration**: `yourproject/__init__.py` imports `nodes`,
  which registers the kinds. `--adapter yourproject` is exactly this
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
  through `python -m dskit.journal research` (never Write
  `docs/research/` by hand). Wrap `live.main` in
  `dskit.journal.hooks.production`. An uninitialized child refuses.
- The skeleton's file list is pinned in dskit's
  `tests/children/test_skeleton.py` — reshaping the SKELETON means
  updating that pin in the same commit (copies are unpinned).

## Layout

```
yourproject/           # tier-3 code (connectors.py, nodes.py)
configs/               # asset-model / source-sample / suite-sample / run-sample
journal.json           # dskit.journal marker
docs/decisioning/      # actions.csv + path.csv; README generated
docs/research/         # research markdown
tests/                 # conftest bootstrap + configs/connectors/nodes tests
pyproject.toml         # dependencies = ["dskit"]
```

Keep this tree and README.md's current when files change.