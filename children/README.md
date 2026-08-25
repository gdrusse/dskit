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
└── tests/
    ├── conftest.py           # sys.path bootstrap — works in-repo and after graduation
    ├── test_connectors.py    # the connector contract + an acquire→validate e2e
    ├── test_nodes.py         # conformance_suite over the kinds + a document e2e
    └── test_configs.py       # every config validates against its engine
```

## The rules

- **Never edit dskit.** A capability the toolkit lacks is either a genuinely
  generic gap — propose an ADR in `docs/architecture/decision-log.md` — or it
  is domain logic and stays here. There is no third option.
- **The domain lives in configs.** Node params, source knobs, suites, the asset
  model: JSON with `notes`, default-deny everywhere. Code holds mechanisms.
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

Worked sketches of real candidates: `docs/architecture/child-gap-pmquant.md`,
`docs/architecture/child-gap-rl-stocks.md`.
