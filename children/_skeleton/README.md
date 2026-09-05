# yourproject — a dskit child

> Template. Starting a child is a copy + a GLOBAL replace (nothing is
> exempt — code, tests, configs, prose), then delete this block:
> `cp -r children/_skeleton children/<name> && cd children/<name> &&
> grep -rl yourproject . | xargs sed -i 's/yourproject/<name>/g' &&
> mv yourproject <name>`

A child consumes dskit, never modifies it: tier-3 code plus JSON configs
over the three seams — a connector (onboarding), registered node kinds
(pipeline), its own asset model (assets). The domain lives in `configs/`.

## Running a model, end to end

Every command runs from the child's own root and none references where
the child lives. Steps 2–4 get data in; step 5 models it.

```bash
python -m pytest tests -q                        # 0. the suite (works uninstalled)
pip install -e .                                 # 1. install, once

python -m dskit.onboarding init --root ./ob      # 2. the onboarding root
python -m dskit.onboarding register-source mysource --root ./ob \
    --catalog-source mysource-src \
    --connector yourproject.connectors:SampleConnector \
    --config @configs/source-sample.json --activate

python -m dskit.onboarding acquire --root ./ob \  # 3. pull history, then check it
    --source mysource --stream samples --mode backfill
python -m dskit.onboarding validate --root ./ob \
    --suite configs/suite-sample.json --snapshot <snapshot-vid>

python -m dskit.onboarding certify --root ./ob \  # 4. OPTIONAL — governance only
    --result <result-vid> --decision certified --by you
python -m dskit.onboarding publish --root ./ob \
    --dataset mydata --certification <cert-vid>

python -m dskit.pipeline run configs/run-sample.json \   # 5. fit / score
    --asof 2026-01-01 --adapter yourproject
# execute rows land in docs/decisioning/actions.csv automatically

python -m dskit.journal research "a question" --topic a-question  # 5b. research note
python -m dskit.journal promote A0001 --criteria empirical  # owner path

python -m dskit.assets init --store ./yourproject_store \  # 6. OPTIONAL — a
    --model configs/asset-model.json                       #    governed catalog
```

Each command prints the `version_id` the next one wants.

**`--root` is on every onboarding command and defaults to
`./onboarding_root`.** Omit it after `init --root ./ob` and you register
into a second, empty root — the most common way this goes wrong.

**Steps 4 and 6 are not on the modelling path.** `acquire` writes
`observations/<source>/`, which the pipeline's data node reads back;
`publish` writes `published/<dataset>/`, read only by the assets
catalog. A run never waits on a certification.

**`--adapter yourproject` is just an import** — importing the package
registers its node kinds, which is what makes `yourproject-*` in the
document resolve.

**Going live:** register a SECOND source over the SAME connector class
with different knobs, then `acquire --mode live` on a cadence.
Checkpoints are keyed per (source, stream, mode), so the backfill and
live cursors never fight — two configs, never two classes.

**Walk-forward:** a document carrying a `walkforward` section runs with
`walkforward` in place of `run`.

Exit codes: `0` ran · `3` halted at a gate (a halt is a result) · `1`
error.

**Journal.** Every acquire / research / execute / production lands in
`docs/decisioning/`. Open that README for the process (it is generated
from CSV). Acquire and execute record themselves; research is
`python -m dskit.journal research`; production wraps `live.main`.
Path to production is owner `journal promote`.

**Memos.** Put durable implementation handoffs, completed-study evidence, and
operational caveats in `docs/memos/`. They are ordinary reviewed documents,
not ADRs and not journaled research. The skeleton keeps the folder present via
`.gitkeep`, so every copied child starts with it.

> The skeleton's sample data node is self-contained, so step 5 works
> before step 2. A real child's data node reads the store, so there the
> order is real.

**Worked instance:** `children/intraday_poc` is exactly this shape —
Alpaca bars (SIP for the backfill, IEX for the live pull), an LSTM per
symbol, and a forward loop that restores the trained artifacts and
trades paper.

> **Read it as a shape, not as a model of good code.** A 2026-08-27 audit
> found ~20 defects in it, six of them silent-wrong-behavior — including a
> live loop that re-implements its own training transform and has drifted
> from it. They are listed in the repo's `TODO.md`. Copy its *structure*;
> check `TODO.md` before copying any of its *code*.

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
├── docs/decisioning/      # generated grid (CSV store; ADR-0056)
│   ├── README.md          # GENERATED — do not edit
│   ├── actions.csv
│   └── path.csv
├── docs/explanations/     # child-specific tutorials and walkthroughs
│   └── README.md          # use record-explanation
├── docs/memos/            # durable implementation and operational handoffs
│   ├── README.md          # use memo
│   └── .gitkeep
├── docs/research/         # research agent markdown
│   ├── README.md          # use record-research
│   └── .gitkeep
├── journal.json           # walk-up marker for dskit.journal
└── tests/                 # green in-repo AND after graduation, uninstalled
    ├── conftest.py        # sys.path bootstrap (position-independent)
    ├── test_configs.py    # every config validates against its engine
    ├── test_connectors.py # four-verb contract + acquire→validate e2e
    └── test_nodes.py      # conformance suite + a document e2e
```

Graduation is a directory move — nothing here references its incubation
position (ADR-0021; see `children/README.md` in the dskit repo).
