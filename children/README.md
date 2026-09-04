# children/ — child projects (ADR-0021)

A **child** is how a real project uses dskit: a thin tier-3 package — pipeline
nodes, connectors, maybe a store backend — plus the JSON configs that carry the
domain. dskit never imports a child, never ships one, and never learns its
domain. This directory incubates children until they graduate to their own
repositories, **unchanged**.

## Start one

```bash
cp -r children/_skeleton children/myproject && cd children/myproject
grep -rl yourproject . | xargs sed -i 's/yourproject/myproject/g'   # macOS: sed -i ''
mv yourproject myproject                   # the package directory itself
python -m pytest tests -q                  # its suite runs standalone...
cd ../.. && python -m pytest tests/children -q   # ...and inside dskit's suite
```

The rename is deliberately a **global replace** — it must reach the tests'
imports, the `yourproject-*` kind names, and the configs, not just
`pyproject.toml`. Nothing is exempt.

`_skeleton/` is the pinned canonical shape — a RUNNABLE child exercising all
three seams (its file list is pinned in `tests/children/test_skeleton.py`;
changing the skeleton means updating that pin in the same commit):

```
_skeleton/
├── README.md                 # what the child does + its one-command runs
├── CLAUDE.md                 # agent orientation for working inside the child
├── pyproject.toml            # the child is its own installable project, depending on dskit
├── yourproject/
│   ├── __init__.py           # import = registration of the kinds below
│   ├── connectors.py         # onboarding seam: your vendor pulls (four verbs)
│   └── nodes.py              # pipeline seam: your node kinds, default-deny params
├── configs/
│   ├── asset-model.json      # storage seam: your catalog's kinds, as config
│   ├── source-sample.json    # a connector config
│   ├── suite-sample.json     # a validation suite
│   └── run-sample.json       # a pipeline document
├── docs/decisioning/         # generated grid (CSV is the store; ADR-0056)
│   ├── README.md             # GENERATED from actions.csv / path.csv
│   ├── actions.csv
│   └── path.csv
├── docs/research/            # research agent markdown
│   └── .gitkeep
├── journal.json              # walk-up marker for dskit.journal
└── tests/
    ├── conftest.py           # sys.path bootstrap — works in-repo and after graduation
    ├── test_connectors.py    # the connector contract + an acquire→validate e2e
    ├── test_nodes.py         # conformance_suite over the kinds + a document e2e
    └── test_configs.py       # every config validates against its engine
```

## The rules

- **Never PATCH dskit from inside a child — but a generic gap does graduate
  INTO dskit.** These are the same rule, and it is the one most often
  misread. A capability the toolkit lacks is either:
  1. **a genuinely generic gap** → it gets BUILT IN `dskit/` (core, or a
     tier-2 `libs/` pack), via an ADR in
     `docs/architecture/decision-log.md`, and the child then calls it; or
  2. **domain logic** → it stays here.

  There is no third option — and "generic" is the common case, not the rare
  one. The test is *might a second project want this?*, not *will it*. A
  rolling-window transform, a validator, a store backend: all generic, all
  belong upstream even though you discovered them while building a child.
  What the rule forbids is a child reaching into `dskit/` to special-case
  itself, or quietly keeping generic capability local because upstreaming
  felt like more work. See root `CLAUDE.md` → "Working agreement".
- **The child IS the adapter unit (ADR-0032).** There is no
  `pipeline_<venue>` package — not in dskit, not beside it. Nodes,
  connectors, backend tags, tracking sinks, and asset models for a project
  all live in that project's one child package; a real venue split is a
  MODULE inside it (`nodes_<venue>.py`), never a package taxonomy.
- **The domain lives in configs.** Node params, source knobs, suites, the asset
  model: JSON with `notes`, default-deny everywhere. Code holds mechanisms.
- **Decisioning is a journal (ADR-0056).** `journal.json` is the
  walk-up marker. Every action is acquire / research / execute /
  production and lands in `docs/decisioning/actions.csv`; the README
  is generated. Path to production is owner `journal promote` only.
  Hooks record pipeline runs and onboarding verbs automatically.
  Research writes `docs/research/<slug>.md`. An uninitialized child
  refuses. The process is in each child's `docs/decisioning/README.md`
  (generated) and in `dskit/journal/README.md`.
- **A vendor knob is a `spec()` knob.** If it selects WHAT you pull — bar
  interval, feed, adjustment, universe — it is config, not a constant in
  `_fetch`. The test: would a second project want it different? Then it
  cannot be a literal.
- **A serving loop READS the configs; it never restates them.** The driver
  writes the whole training document to `<run-dir>/config.json`, so lookback,
  gap discipline, and the trainer node keys are already on disk — read them.
  Vendor knobs come from the source config the puller already uses. Only
  operational flags (quantity, dry-run, log dir) belong on the CLI. A live
  path that re-declares any of this WILL drift from the backtest, and a
  parity test is a patch over the duplication, not a fix for it. Never add a
  third config file to solve this — that duplicates both.
- **"One per key" is a grammar problem, not a config problem.** A design
  fitting one model per symbol/venue/cohort currently has to be written
  longhand — N filter + N train + N score nodes per document. Before hand-
  expanding a fan-out, check `TODO.md`: the generic `foreach` gap is open,
  and hand-expansion is the interim, not the answer.
- **Position-independent.** Nothing inside a child may reference its incubation
  path (no `..` imports, no dskit-repo paths); the only coupling is
  `import dskit`. That is what makes graduation a plain `cp -r`.
- **Tests ship or the child fails.** The root suite runs every
  `children/<project>/` by subprocess; a child without `tests/` is a failure,
  not a skip. Heavy deps: `pytest.importorskip` inside the child's tests.
- Tier-3 may import anything — but keep heavy imports inside `run()`/`read()`
  anyway; your plans stay cheap.

## Graduate

When a child earns its own repo: copy `children/<project>/` out as the new
repo's root, `pip install -e .` there, run its tests, delete the directory
here. Nothing inside changes — that was the point.

Incubating today: `intraday_equities` (US-equity intraday bars),
`intraday_poc`, and `pmquant` (prediction-market ladders — Kalshi and
Polymarket; its `configs/run-e2e.json` runs the stat test, the
transformer and the Kelly MIO in one document). Worked sketches of the
gap analyses that preceded them: `docs/architecture/child-gap-pmquant.md`,
`docs/architecture/child-gap-rl-stocks.md`.
