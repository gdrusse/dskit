# dskit.assets

The **Data Asset Platform**: one config-driven registry engine. A JSON
*asset model* declares kinds (fields, refs, lifecycle); a *store* is
created pinned to that model's hash; a *registry* is the only mutation
path. The master spec's 13 registries ship as the built-in default model
(12 kinds + lineage native in the engine).

Identity is a content hash: `version_id = sha256` over canonical
`{kind, payload, refs}` (`notes` stripped — documentation never changes
identity). Same content, same asset — reuse before duplication. Records
are write-once; lifecycle state is derived from an append-only event log.

## The 60-second path

```bash
python -m dskit.assets validate-model                 # the default model + its hash
python -m dskit.assets init --store ./asset_store     # a store pinned to it
python -m dskit.assets register entity --payload '{"name": "AAPL"}' --store ./asset_store
python -m dskit.assets register feature --payload '{"name": "mom_20d"}' \
    --ref entity=<version_id> --store ./asset_store   # refused without the entity ref
python -m dskit.assets transition <vid> validated --store ./asset_store
python -m dskit.assets ingest-run <run_dir> --store ./asset_store   # observe a pipeline run
python -m dskit.assets lineage <vid> --show ancestors --store ./asset_store
```

Exit codes: **0** ok · **1** error (every problem listed, one per line).

## Writing your own model

A model is a JSON file — see
[`examples/assets/custom-model.json`](../../examples/assets/custom-model.json)
for a worked one. Per kind:

- `fields`: name → `{"type": <one of the six JSON types>, "required": bool}`.
  Every kind must declare a **required string `name`** (the human alias).
- `refs`: name → `{"kind": <target>, "required": bool}` — governance as
  topology; the target kind must be declared in the same model, and a
  ref's value must be a version_id already in the store.
- `lifecycle`: `{"states": [...], "initial": ..., "transitions": {...}}`
  — default-deny; omit the block entirely for record-only kinds.

Pass it everywhere with `--model path.json` (or `load_model(path)` in
code). The store's pin guarantees a store is only ever driven by the
model it was created with.

## Extending

- **Storage** — subclass the `Store` ABC (`store.py`). The tier-1
  `FileStore` is single-writer, scan-query JSON (fine to ~10^4 assets);
  sqlite/postgres/parquet belong in tier-2 `libs/` packs.
- **Semantics** — the engine checks structure only (the six JSON types
  plus ref topology). Set-membership integrity, date semantics and the
  like are the declared future seam: enforce them in your own layer
  above `Registry`.
- **Observation** — `ingest_run` reads a completed pipeline run
  directory; nothing imports across the pipeline boundary in either
  direction.
- **Registration** — `sync_published` scans a published outbox root
  (`published/<dataset>/*.json` manifests, e.g. `dskit.onboarding`'s)
  and registers dataset versions idempotently; the scan is delivery
  and anti-entropy in one. The dataset alias must already be cataloged.

## Contents

```
dskit/assets/
├── __init__.py        public surface: Registry, FileStore, default_model, ...
├── base.py            canonical hash (pipeline parity), validation, atomic writes
├── model.py           the model grammar: AssetModel / KindSpec / FieldSpec / RefSpec
├── default_model.py   the spec's 12 kinds + lifecycle, shipped as data
├── record.py          AssetRecord: {kind, payload, refs} -> version_id
├── store.py           Store ABC + JSON FileStore (write-once, append-only events)
├── registry.py        the engine: register / get / find / list / state / transition
├── lineage.py         one global DAG: provenance-stamped edges + end-to-end queries
├── ingest.py          ingest_run: observe a completed pipeline run dir
├── sync.py            sync_published: scan a published outbox root (ADR-0012)
├── __main__.py        the CLI: python -m dskit.assets
├── README.md          this file
└── CLAUDE.md          agent orientation
```

Tests: `python -m pytest tests/assets -q` (includes the purity gate, the
pipeline hash-parity test, and an end-to-end ingest of a real run).
