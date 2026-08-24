# CLAUDE.md — dskit.onboarding

Orientation for an agent working inside this package. Read the package
[README.md](README.md) first for what it does; this file is how to work
on it without breaking its rulings (ADR-0012…0016).

## Conventions

- **Reuse, never copy** (ADR-0013): identity hashing, `AssetError`, and
  the checker idiom come from `dskit.assets.base` via `base.py`. If you
  need a mechanic the assets engine has, import it — do not re-implement.
- **Errors accumulate**; validation in `__post_init__`; `from_obj`
  default-denies unknown keys — the assets/pipeline config idiom exactly.
- **Durability ordering is the design.** raw/ and published/ writes go
  through `durable_write_*` (stage, fsync, rename, fsync dir); the
  checkpoint is saved LAST in `run_acquisition`. Reordering these is a
  correctness bug even when every test still passes.
- **Declared, never inferred**: mode (`backfill|live`), record kind
  (`observation|forecast`), and certification decisions are closed
  vocabularies stamped as fields. Never derive any of them from dates.

## Extension points

- **Connectors** — subclass `Connector`; reference by `pkg.module:Class`
  (import = registration) or add a tier-2 pack in `libs/` +
  `DEFAULT_CONNECTORS`. Conformance template:
  `tests/onboarding/test_localfiles.py`.
- **Validation rules** — add to `_RULES` in `validate.py`:
  `(allowed_kwargs, required_kwargs, evaluator)`; the evaluator returns
  a failing COUNT, nothing else. Structure-level only — semantics stay
  the declared seam.
- **The domain model** — kinds are config (ADR-0007). New evidence kinds
  go in the model, but see the pin gotcha below.

## Gotchas

- **The model hash is pinned twice**: `tests/onboarding/test_default_model.py`
  pins it AND asserts parity with `docs/architecture/onboarding-model.json`.
  Changing `default_model.py` means updating the doc, the pin, and
  (because it is ratified design) the decision log — in the same commit.
- **The purity gate forbids `dskit.pipeline`** entirely (its own static
  test) — the engine/sibling firewall crosses only via files on disk.
- **`raw/` and `published/` are WORM.** `write_snapshot` refuses an
  existing acq_id; `publish` refuses to overwrite; `verify` re-hashes.
  Anything that "fixes" a snapshot in place defeats the evidence model.
- **Publish idempotency keys on the CERTIFICATION**, not the manifest
  hash — the default label embeds the outbox sequence number, so
  content-hash dedupe alone would re-mint versions.
- **A crash between snapshot and checkpoint re-pulls** — that is
  intended (at-least-once + hash-keyed dedupe = effectively-once). Do
  not "fix" duplicate snapshots by moving the checkpoint earlier.
- **`FakeConnector` scripts are class attributes** (the acquire path
  instantiates the class); the `fake_source` fixture resets them.

## Contents

```
dskit/onboarding/
├── __init__.py        public surface (curated re-exports only, no logic)
├── base.py            assets re-exports + durable writes + file_digest + parse_utc
├── default_model.py   the ratified P2 model as data (pin: a8775903...)
├── layout.py          OnboardingRoot — every path; create-exactly-once
├── connector.py       Connector ABC, envelope checks, config default-deny, resolve
├── state.py           load_state / save_state — (source, stream, mode) cursors
├── snapshot.py        build_manifest / write_snapshot / verify / find_snapshot_dir
├── acquire.py         run_acquisition — the orchestrated pull + durability order
├── validate.py        Rule / ValidationSuite / _RULES / run_suite
├── certify.py         certify — decisions; block-cannot-certify gate
├── publish.py         publish_version — outbox manifests, certification-keyed
├── libs/
│   ├── localfiles.py  reference connector (stdlib CSV/JSONL)
│   └── restapi.py     declarative REST connector (stdlib urllib; scripted
│                      `_fetch` seam — tests never touch the network)
├── __main__.py        CLI
├── README.md          user-facing docs
└── CLAUDE.md          this file
```

Keep both trees (here and in README.md) current when files change.
