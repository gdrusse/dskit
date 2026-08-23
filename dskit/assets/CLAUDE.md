# CLAUDE.md — dskit.assets

Orientation for an agent working inside this package. Read the package
[README.md](README.md) first for what it does; this file is how to work
on it without breaking its rulings.

## Conventions (all inherited from the pipeline's config layer)

- **Errors accumulate.** Validation helpers append to an error list;
  one `AssetError` reports every problem. Never raise on the first.
- **An invalid object can never exist** — validation runs in
  `__post_init__`; `from_obj` rejects unknown keys (default-deny).
- **Canonical hash = the pipeline's recipe, byte for byte** (sorted
  keys, no whitespace, ASCII, NaN refused, `notes` stripped at every
  level). `tests/assets/test_base.py::test_hash_parity_with_pipeline`
  enforces it — never "improve" the recipe.
- **Write-once + append-only.** Records never change after `put`;
  state is derived by replaying events. Anything that looks like an
  update is a new version or a new event.

## Extension points

- `Store` ABC (`store.py`) — the only sanctioned seam for new storage.
  Tier-2 packs go in `libs/<name>.py`, heavy imports inside methods.
- The **semantic seam**: the engine checks JSON structure + ref
  topology only. Do not add domain semantics to the engine; they belong
  in models (`notes`) or layers above `Registry`.
- New kinds are **model config, never code** (ADR-0007). Package 2's
  kinds will arrive as a model document.

## Gotchas

- `notes` is **reserved** as a payload field / ref name — the hash
  recipe strips it, so `check_payload` refuses it.
- The **default model's hash is pinned** in
  `tests/assets/test_default_model.py`. Editing `default_model.py`
  means updating the pin in the same commit, deliberately.
- `FileStore` is **single-writer**; kind names must be
  filesystem-safe (lowercase/digits/`_`/`-`). Both are declared limits,
  not bugs to fix here — a tier-2 store is the fix.
- The purity gate (`tests/assets/test_purity.py`) fails on ANY
  module-level import outside stdlib + this package — heavy imports go
  inside functions, as in the pipeline.
- Ingest payloads must stay **pure functions of run-dir content** — no
  timestamps, paths, or machine names — or re-ingest stops being
  idempotent.

## Contents

```
dskit/assets/
├── __init__.py        public surface (curated re-exports only, no logic)
├── base.py            AssetError, canonical_hash, checkers, atomic_write_json, utc_now
├── model.py           AssetModel / KindSpec / FieldSpec / RefSpec, load_model, model_hash
├── default_model.py   the spec's 12 kinds as data; DEFAULT_LIFECYCLE
├── record.py          AssetRecord, check_payload
├── store.py           Store ABC, FileStore
├── registry.py        Registry — the only mutation path
├── lineage.py         Lineage — DAG edges, cycle-refusing, phase-stamped
├── ingest.py          ingest_run — the ADR-0008 file seam
├── sync.py            sync_published — the ADR-0012 outbox scan
├── __main__.py        CLI
├── README.md          user-facing docs
└── CLAUDE.md          this file
```

Keep both trees (here and in README.md) current when files change.
