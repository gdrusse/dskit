# dskit

A toolkit of **generalizable packages for data-science and ML projects**, built
on one doctrine: *code is generic, configuration is the interface, and your
project is a thin child.* Every behavior — which data to pull, what a valid
dataset is, which model trains, what gates a deployment — lives in a JSON
document with a stable identity hash. The packages never learn your domain;
you never edit the packages.

## The three pillars

| Pillar | Package | One line |
|---|---|---|
| **Pull** | `dskit.onboarding` | connectors acquire data into immutable WORM snapshots; declarative suites validate; certification + publication make evidence |
| **Store** | `dskit.assets` | a config-driven registry/catalog: content-addressed records, lifecycles, lineage — your asset model is a JSON document, kinds included |
| **Run** | `dskit.pipeline` | one JSON document declares a whole process as a node DAG; one command plans, runs, and records it |

They compose by files, never imports: onboarding **publishes** pointer manifests
that assets **syncs** into the catalog; pipeline runs leave run dirs that assets
**ingests** as observations. Each pillar stands alone; together they are pull →
store → run with provenance end to end.

## Install

```bash
pip install -e .            # the tier-1 core is pure stdlib — no heavy deps
pip install -e ".[dev]"     # + pytest / hypothesis / pytest-cov / ruff
pip install -e ".[all]"     # + every optional library the tier-2 packs can use
```

The core has **zero required dependencies** by design: documents must stay
plannable on a machine with nothing installed. Heavy libraries are optional
extras, imported only inside a node's `run()` / a connector's `read()`.

## 60 seconds per pillar

**Run** — a document in, a run directory out:

```bash
python -m dskit.pipeline nodemap                                  # full demo run
python -m dskit.pipeline run examples/pipeline/nodemap-minimal.json --asof 2026-01-01
python -m dskit.pipeline plan <doc.json>                          # resolved DAG, no execution
```

Exit codes: **0** ran · **3** halted at a NO-GO gate (a halt is a result) ·
**1** error. Identity = sha256 of the canonical JSON (`notes` stripped
everywhere; `env`/`outputs`/`schedule` excluded). Same hash, same experiment.

**Store** — a catalog governed by a model you declare:

```bash
python -m dskit.assets init --store ./asset_store                  # default model; --model for your own
python -m dskit.assets register source --payload '{"name": "vendor"}' --store ./asset_store
python -m dskit.assets register dataset --payload '{"name": "prices"}' \
    --ref source=<source-vid> --store ./asset_store                # datasets carry provenance — the ref is required
python -m dskit.assets list --store ./asset_store                  # content-addressed version_ids
python -m dskit.assets state <dataset-vid> --store ./asset_store   # lifecycle governance: "draft"
```

`register` prints the new version_id — that is what the `<...-vid>`
placeholders take. Records are write-once, state replays from an append-only event log, and the
store backend (`file`/`sqlite`/`parquet` — or your own `pkg.module:Class`) is
declared in `store.json`, not in code.

**Pull** — acquire, validate, certify, publish:

```bash
python -m dskit.onboarding init --root ./ob
python -m dskit.onboarding register-source vendor --catalog-source vendor-src \
    --connector localfiles --config '{"path": "./data", "effective_field": "date"}' --activate
python -m dskit.onboarding acquire  --root ./ob --source vendor --stream prices --mode backfill
python -m dskit.onboarding validate --root ./ob --suite examples/onboarding/suite-basic.json --snapshot <vid>
python -m dskit.onboarding certify  --root ./ob --result <vid> --decision certified --by you
python -m dskit.onboarding publish  --root ./ob --dataset vendor-prices --certification <vid>
python -m dskit.assets sync-published ./ob/published --store ./asset_store   # into the catalog
```

Every record carries `(effective_date, acquired_at)` — what the data describes
vs when you got it — and a declared `backfill`/`live` mode with its own cursor.

## Your project is a child

One test decides where code goes: *could a project that has never heard of your
problem domain use it?*

| Tier | Path | Rule |
|---|---|---|
| 1. Core | `dskit/<pkg>/*.py` | stdlib only; domain-neutral |
| 2. Library packs | `dskit/<pkg>/libs/<lib>.py` | generic wrappers for standard DS/ML libraries; name the library only inside `run()` |
| 3. Your project | a **child** package, outside `dskit` | domain-specific; may import anything |

A child is thin: tier-3 wrappers (pipeline nodes, connectors, store backends)
in a few clear files, plus the JSON configs that carry the domain — sources,
suites, an asset model, run documents. **The child is the whole adapter**
(ADR-0032): there is no `pipeline_<venue>` package, in dskit or beside it —
a venue split is a module inside the child. The toolkit never imports a child;
`children/README.md` is the guide, `children/_skeleton/` the runnable template,
and `children/<project>/` the incubator until a child graduates to its own repo.

Each package's own `README.md` covers its config grammar and extension seams:
[pipeline](dskit/pipeline/README.md) · [assets](dskit/assets/README.md) ·
[onboarding](dskit/onboarding/README.md). Design history lives in
`docs/architecture/decision-log.md` (ADRs — no decision undocumented).

## Tests

```bash
pip install -e ".[dev,all]"
python -m pytest -q                       # full suite (children run by subprocess)
python -m pytest tests/pipeline -q        # engine core + purity gate
python -m pytest tests/assets tests/assets_libs -q
python -m pytest tests/onboarding -q
```

Purity gates enforce the tier rules mechanically: the cores import nothing
heavy, ever.
