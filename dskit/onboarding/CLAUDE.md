Default answer: outcome first, max 5 lines. Expand only if I ask.

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
- **`acquired_at` is the COMMIT instant** (ADR-0079): `utc_now()` taken
  after `read()` is exhausted and settled onto the staged rows line by
  line — a capture stream dating rows at its own clock never races it;
  only a genuinely future-dated observation refuses.
- **Declared, never inferred**: mode (`backfill|live`), record kind
  (`observation|forecast`), and certification decisions are closed
  vocabularies stamped as fields. Never derive any of them from dates.

## Extension points

- **Connectors** — subclass `Connector`; reference by `pkg.module:Class`
  (import = registration) or add a tier-2 pack in `libs/` +
  `DEFAULT_CONNECTORS`. Conformance template:
  `tests/onboarding/test_localfiles.py`. A binary artifact (a model's
  weights) is a `FILE` message (ADR-0082): the connector names a file it
  holds, the platform copies it into `payload/<stream>/<relpath>`;
  `libs/huggingface.py` is that shape — `resolve` / `download` seams,
  one FILE + one inventory RECORD per file, and a SELECTION cursor (sha +
  repo type + both pattern lists, so a widened `allow_patterns` at an
  unchanged sha is new content); a download matching no file refuses and
  moves no cursor. `libs/localtables.py` is the
  tier-2 shape: the library (pyarrow) imported inside the verbs, only
  when a shard needs it, refused loudly when absent. `libs/predexon.py`
  is the keyed-REST shape: getter, clock, and sleeper injected through
  the constructor so pacing and retry are tested with no network and no
  wait; its cursor nests per ticker under the stream key. `libs/kalshi.py`
  is the public-REST shape: its `markets` stream is a deliberate full
  re-pull (settlement lands after close; dedup keeps the latest), and rows
  the venue does not date are stamped at the pull's capture MINUTE so two
  pulls inside a minute collide on their key (`acquired_at` itself is the
  commit instant, ADR-0079, so a capture instant never post-dates it).
- **OAuth connectors** — expose `oauth_service(config)` returning
  `OAuth2TokenService`; the CLI stays provider-polymorphic.
- **Recurring pulls** — call `run_watch`; it repeats `run_acquisition`
  and deliberately adds no retry, daemon, or market-session policy.
- **Reading observations back** — consumers (children, packs) go
  through `observations.scan_stream` / `stream_digest` (ADR-0037),
  never a hand-rolled glob: the seam owns codec resolution, bitemporal
  dedup, and the single-copy memory contract. A FILE tree is read back
  through `observations.verified_payload_dir(root, manifest_hash,
  stream)` (ADR-0083): located by hash, re-hashed, refused on drift.
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
- **A FILE's bytes are read AS the message arrives** (ADR-0082): the
  platform copies `path` inside the read loop, so a connector may delete
  its staging once `read()` ends — and a consumer of `read()` (a test)
  must open the file before asking for the next message. A FILE is never
  echoed into bronze (its `path` is machine-local): identical bytes
  pulled twice lay out identical payload trees.
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
- **The acquire writer stack closes BEFORE `build_manifest`** — with a
  gzip codec this ordering is load-bearing, not style: a member digested
  without its trailer would verify forever over undecodable bytes. The
  pre-commit `verify_member` pass is the guard; do not remove either.
- **Never extend `_MANIFEST_KEYS` for codecs** (ADR-0036): the codec is
  the file EXTENSION, already inside `relpath`/the Merkle hash. A
  manifest field would break every older reader by default-deny and add
  a second declaration that can disagree with the filename.
- **`storage` and `notes` are reserved config keys** — `check_config`
  refuses a connector spec that declares either; acquire strips
  `storage` before the connector sees config.
- **Journal (ADR-0056).** ``__main__`` function-imports
  ``dskit.journal`` after acquire/validate/certify/publish/register-source
  and once at watch start. Module-level is still illegal. Pytest is a
  no-op. An uninitialized child refuses.
- **The coverage ledger never guesses a calendar** (ADR-0030): `missing`
  takes the caller's DECLARED period list; do not "improve" it with
  range inference — that blind spot is the bug class it exists to
  prevent. One writer per ledger file; `reconcile` adopts store truth
  but never clears a `fetched` claim (that is the operator's `clear`).
- **`polymarket` seams are METHODS** (`get_json`/`post_json`/`download`/
  `sleep`/`now`): script them by subclassing (`tests/onboarding/
  test_polymarket.py`), never by assigning a plain function at class level
  (it would bind `self`). Its `events` cursor is RECORDED, never consulted
  (markets close on a per-series lag, so a cursor filter dropped late
  closers forever): every pull re-walks the window and dedup keeps the
  latest; `closed: false` rows are `kind: "forecast"` and re-emit every pull.

## Contents

```
dskit/onboarding/
├── __init__.py        public surface (curated re-exports only, no logic)
├── base.py            assets re-exports + durable writes + file_digest + parse_utc
├── default_model.py   the ratified P2 model as data (pin: a8775903...)
├── layout.py          OnboardingRoot — every path; create-exactly-once
├── connector.py       Connector ABC, envelope checks, config default-deny, resolve
├── state.py           load_state / save_state — (source, stream, mode) cursors
├── coverage.py        CoverageLedger — sparse-backfill done-set (ADR-0030)
├── leads.py           LeadGrid — lead-fraction capture grid; due_periods speaks the ledger's period spelling (ADR-0075)
├── codec.py           extension-declared codecs — deterministic gzip (ADR-0036)
├── observations.py    the read seam: scan_stream dedup + stream_digest (ADR-0037); verified_payload_dir (ADR-0083)
├── oauth.py           OAuth2 exchange/refresh + atomic owner-only token files
├── snapshot.py        build_manifest / write_snapshot / verify / find_snapshot_dir
├── acquire.py         run_acquisition — the orchestrated pull + durability order
├── validate.py        Rule / ValidationSuite / _RULES / run_suite
├── certify.py         certify — decisions; block-cannot-certify gate
├── publish.py         publish_version — outbox manifests, certification-keyed
├── libs/
│   ├── alpaca.py      Alpaca Market Data stock bars (optional alpaca-py)
│   ├── alpaca_quotes.py  Alpaca NBBO quotes folded to one bid/ask per minute (stdlib HTTP)
│   ├── huggingface.py one hub repository at a pinned commit: FILE + inventory RECORD per file (ADR-0082)
│   ├── kalshi.py      Kalshi trade-API v2 markets/candles/fee_schedules/orderbooks (stdlib urllib, ADR-0075)
│   ├── localfiles.py  reference connector (stdlib CSV/JSONL)
│   ├── localtables.py parquet / newline-JSON table directories (ADR-0076)
│   ├── polymarket.py  Polymarket Gamma/CLOB REST + pmxt HF hour archive (stdlib urllib; hub + pyarrow inside read, ADR-0075)
│   ├── predexon.py    Predexon Kalshi L2 order-book history (stdlib urllib, ADR-0075)
│   ├── restapi.py     declarative REST connector (stdlib urllib)
│   └── schwab.py      Schwab closed-minute REST bars + OAuth refresh
├── watch.py           repeated finite acquisitions; first error stops
├── __main__.py        CLI
├── README.md          user-facing docs
└── CLAUDE.md          this file
```

Keep both trees (here and in README.md) current when files change.