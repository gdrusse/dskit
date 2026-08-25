# yourproject — a dskit child

> Template. Starting a child is a copy + a GLOBAL replace (nothing is
> exempt — code, tests, configs, prose), then delete this block:
> `cp -r children/_skeleton children/<name> && cd children/<name> &&
> grep -rl yourproject . | xargs sed -i 's/yourproject/<name>/g' &&
> mv yourproject <name>`

A child consumes dskit, never modifies it: tier-3 code plus JSON configs
over the three seams — a connector (onboarding), registered node kinds
(pipeline), its own asset model (assets). The domain lives in `configs/`.

## The one-command runs

All from the child's own root — the commands never reference where the
child lives:

```bash
python -m pytest tests -q                  # the child's suite (works uninstalled)
pip install -e .                           # then:
python -m dskit.pipeline run configs/run-sample.json --asof 2026-01-01 --adapter yourproject
python -m dskit.assets init --store ./yourproject_store --model configs/asset-model.json
```

## Layout

```
_skeleton/
├── pyproject.toml         # dependencies = ["dskit"] — the only coupling
├── README.md / CLAUDE.md  # this file; agent orientation
├── yourproject/           # tier-3 code; import = registration
│   ├── __init__.py        # curated re-exports
│   ├── connectors.py      # onboarding seam: the vendor pull (four verbs)
│   └── nodes.py           # pipeline seam: node kinds, default-deny params
├── configs/               # the domain, as self-documenting JSON
│   ├── asset-model.json   # the child's catalog kinds
│   ├── source-sample.json # a connector config object
│   ├── suite-sample.json  # a validation suite
│   └── run-sample.json    # a pipeline document
└── tests/                 # green in-repo AND after graduation, uninstalled
    ├── conftest.py        # sys.path bootstrap (position-independent)
    ├── test_configs.py    # every config validates against its engine
    ├── test_connectors.py # four-verb contract + acquire→validate e2e
    └── test_nodes.py      # conformance suite + a document e2e
```

Graduation is a directory move — nothing here references its incubation
position (ADR-0021; see `children/README.md` in the dskit repo).
