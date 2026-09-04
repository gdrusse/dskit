# dskit.onboarding

**Acquisition & Onboarding**: connectors pull data (backfill or live),
every pull lands as an immutable WORM snapshot with a Merkle manifest,
declarative JSON suites validate it, a certification records the
decision over the evidence, and publication writes a pointer manifest
into an outbox that `dskit.assets` scans and registers. Evidence lives
in a P2-local assets store governed by the built-in onboarding model —
the assets engine reused as a library, kinds as config.

Two axes ride on every record: `(effective_date, acquired_at)` — what
time the data *describes* vs when you *got* it — and **mode**
(`backfill` | `live`), a declared field with per-(source, stream, mode)
checkpoint cursors, never an inference from dates. `acquired_at` is the
pull's COMMIT instant — stamped after `read()` finishes (ADR-0079), so a
connector may date a live capture at its own clock, unfloored.

For sparse backfills over units × periods (tickers × days, stations ×
months) the **coverage ledger** (ADR-0030) is the finer primitive: a
SQLite done-set keyed `(source, stream, unit, period)` with
`fetched`/`no_data` statuses. `CoverageLedger.from_root(root)` opens it
at `state/coverage.sqlite`; `mark`/`missing`/`stale_units` drive the
backfill loop (expected periods are DECLARED by the caller — the ledger
never guesses a calendar), `audit`/`reconcile` keep it honest against
the store. Library-first: acquisition never consults it implicitly.

## The 60-second path

```bash
python -m dskit.onboarding init --root ./onboarding_root   # --backend sqlite/parquet for a tier-2 store
python -m dskit.onboarding register-source vendor --root ./onboarding_root \
    --catalog-source vendor-src --connector localfiles \
    --config '{"path": "./data", "effective_field": "date"}' --activate
python -m dskit.onboarding acquire --root ./onboarding_root \
    --source vendor --stream prices --mode backfill      # WORM snapshot + evidence
python -m dskit.onboarding watch --root ./onboarding_root \
    --source vendor --stream prices --mode live --every-seconds 60
python -m dskit.onboarding validate --root ./onboarding_root \
    --suite examples/onboarding/suite-basic.json --snapshot <vid>
python -m dskit.onboarding certify --root ./onboarding_root \
    --result <vid> --decision certified --by you
python -m dskit.onboarding publish --root ./onboarding_root \
    --dataset vendor-prices --certification <vid>        # into the outbox
python -m dskit.assets sync-published ./onboarding_root/published \
    --store ./asset_store                                # the P1 half
python -m dskit.onboarding verify --root ./onboarding_root   # tamper check
```

Exit codes: **0** ok · **3** `validate` gated `block` (a block is a
result) · **1** error (every problem listed, one per line).

## The flow, end to end

```
  register-source ---> source_config (ACTIVE)   the evidence store:
         |             read as a ref, never     every record REFS the
         v             rewritten by a pull      one before it
  +-------------+  stage -> Merkle manifest -> rename into raw/
  |   acquire   |  = THE COMMIT POINT   ---> acquisition_job, snapshot
  +-------------+  the cursor is saved LAST, after that evidence
         |
         |  empty pull: no snapshot, nothing registered — a STATE
         |  message still checkpoints, and is then the ONLY write
         v
  +-------------+  suite JSON -> failing counts -> thresholds
  |   validate  |  ---> validation_result   (ref: snapshot)
  +-------------+
         v
  +-------------+  reads the result's refs, carries the snapshot on
  |   certify   |  ---> certification   (refs: snapshot, result)
  +-------------+
         v
  +-------------+  pointer manifest: snapshot hashes, never data;
  |   publish   |  idempotent on the CERTIFICATION, not the hash
  +-------------+  ---> published_version   (ref: certification)
         v
  dskit.assets sync-published      files on disk are the only seam

  and the way OUT, for consumers of the rows themselves:
  observations/<source>/<acq_id>/<stream>.jsonl[.gz]
         `--> scan_stream(root, source, stream, key_fields=...)
              dedups bitemporally, holds the stream ONCE
```

- **the ACTIVE rule** — `find_active_source` (`acquire.py`) demands exactly
  one `active` config per alias; zero and two are both errors, never a
  guess. `register-source` is the only verb that writes one.
- **the commit point** — the rename inside `write_snapshot` (`snapshot.py`)
  makes a whole snapshot exist at once; a crash before it leaves no debris
  and re-pulls from the old cursor — at-least-once plus hash dedupe.
- **the read seam** — `scan_stream` / `stream_digest` (`observations.py`,
  re-exported from `dskit.onboarding`) is how consumers read rows back:
  bitemporal dedup, the stream held once, and a digest computed without
  ever building the whole-snapshot string. A hand-rolled glob over
  `observations/` is exactly what it replaces (ADR-0037).
- **the pin** — `OnboardingRoot.create` pins the store to `onboarding_model`
  (`default_model.py`) at init; `registry()` only reopens it, and its model
  must hash to that pin — the pin governs, not the accessor.

## Writing a validation suite

A suite is JSON — see
[`examples/onboarding/suite-basic.json`](../../examples/onboarding/suite-basic.json)
for a worked one. Each rule:
`{id, target, rule, kwargs, severity, error_if|warn_if, notes}` —
`target` is a stream; the rule produces a failing count; the threshold
(default `"!= 0"`) gates on it. Built-ins: `not_null`, `unique`,
`accepted_values`, `in_range`, `row_count`, `distinct_count`,
`bitemporal`. `distinct_count` (ADR-0084) is the cardinality rule:
`{"field": "strike", "group_by": "event", "min": 3, "max": 3}` fails one
per group whose distinct non-null `field` values fall outside the bounds
(`group_by` optional — a field name or a list — and ungrouped the whole
stream is the one group; nulls skipped). Tripped
`error` → gating `block`; tripped `warn` → `warn` (warn never blocks).
A `block` result cannot be certified — amend the suite (a new, auditable
hash) instead.

## Writing a connector

Subclass `Connector` (four verbs) and reference it as
`pkg.module:Class` in `register-source --connector` — import is
registration, no entry anywhere needed:

- `spec()` — declare config knobs default-deny; flag secrets (their
  values are env-var NAMES, resolved inside the connector — secret
  material never enters configs or hashes).
- `check(config)` — fail fast; move no data.
- `discover(config)` — `[{stream, schema, primary_key}]`.
- `read(config, streams, state, mode)` — yield plain-dict messages with
  `"protocol": 1`: `RECORD` (`stream`, ISO `effective_date`, `data`,
  optional `kind: "forecast"` to segregate declared forecasts), `STATE`
  (opaque checkpoint — "everything before this is durable"; persisted
  only after the snapshot is), `SCHEMA`, `LOG`, `ERROR`, and `FILE`
  (`stream`, POSIX-relative `relpath`, local `path` — a binary the
  platform COPIES into the snapshot at `payload/<stream>/<relpath>` as
  the message arrives, digested and verified like every payload byte;
  ADR-0082). Unknown types are skipped (forward-compat); unknown keys on
  known types are refused. Heavy imports go INSIDE `read()`.

`libs/localfiles.py` is the worked reference —
`tests/onboarding/test_localfiles.py` drives it through the whole
contract and is the conformance template for yours.

A corpus that already sits on disk as tables needs no connector code
either: the `localtables` kind (ADR-0076) reads a directory of
`parquet` / `ndjson[.gz]` / `jsonl[.gz]` files. `layout: "directory"`
makes each subdirectory a stream whose files are its shards (per-series
files become ONE stream; `stamp_stem_as` writes the series onto each
row), `layout: "file"` makes each file stem a stream; `effective_field`
+ `effective_unit` (`iso | ms | s`) name the row's instant, normalized
to UTC on the envelope and left untouched in `data`. pyarrow (the
`dskit[parquet]` extra) is imported only when a parquet shard is in
scope; `libs/localtables.py` is the knob reference.

Many APIs need no connector code at all: the `restapi` kind DECLARES a
JSON API in config — streams as endpoint paths, a dot-path to the
records, pagination (`none | cursor | page | offset`), one env-var
credential, optional server-side `since` filtering. See
[`examples/onboarding/source-restapi.json`](../../examples/onboarding/source-restapi.json)
for a worked config and `libs/restapi.py` for the knob reference.

Market-bar packs are registered as `alpaca` and `schwab`. Both emit
`bars` keyed by `(symbol, ts)` with provider-neutral OHLCV fields.
Alpaca adds trade count/VWAP; Schwab emits those fields as null and
re-requests a declared live overlap so bitemporal dedup retains the
latest evidence. Alpaca bounds the SDK's in-memory response with
`chunk_days` (default 31). Install `dskit[alpaca]` for Alpaca; Schwab
REST is stdlib-only.

The `polymarket` pack (ADR-0075) is keyless: Gamma `events` (one row per
market, settlement and fee fields; the cursor is recorded, never consulted —
every pull re-walks the `start`/`end` window and dedup keeps the latest,
so late resolutions land without a fresh `start`), derived
`fee_schedules`, CLOB `books`, and the pmxt `archive_hours` (needs
`huggingface_hub` + `pyarrow`; token ids declared or else resolved from the
same Gamma walk, rows keyed `(asset_id, ts, seq)` because several price-level
updates share a millisecond, and the mirror's own sync state telling a
permanent gap — skipped — from an hour not mirrored yet — retried);
`libs/polymarket.py` is the knob reference.

The `kalshi` kind (ADR-0075) pulls Kalshi's public trade API v2 for a
declared `series` basket — `markets` (the 14-field strike/status/result
row, keyed `ticker`, a deliberate full re-pull because settlement lands
after close), `candles` (`(ticker, ts)`), `fee_schedules` and `orderbooks`
(provider-shaped bids, never mirrored) — through one injectable getter with
pacing and 429/5xx retry; `libs/kalshi.py` is the knob reference.

The `predexon` kind (ADR-0075) pulls Predexon's Kalshi L2 order-book
history as one `l2_snapshots` record per sequenced snapshot, keyed
`(ticker, timestamp, sequence)`: ladders normalized to
`[[price_dollars, size], ...]` (YES bids descending, YES asks ascending),
the vendor's `best_*`/`*_depth` fields verbatim, a per-ticker
`{timestamp, sequence}` cursor, pacing and retry through injectable
seams, and the key named by `api_key_env` (default `PREDEXON_API_KEY`).
`libs/predexon.py` is the knob reference; `native_book` there projects a
record into Kalshi-native YES/NO bid ladders.

The `huggingface` kind (ADR-0082) acquires one Hugging Face hub repository
— a pretrained model, or a dataset — as a WORM snapshot: `repo_id` and
`revision` (branch, tag or commit; resolved to the commit sha at pull
time and downloaded AT that sha), optional `repo_type`, `allow_patterns`
/ `ignore_patterns`, and `token_env` (default `HF_TOKEN`; unset means
anonymous — the client is handed `token=False`, so its cached login is
never used). One stream, `snapshot`: per file a `FILE` message and an
inventory RECORD `{repo_id, repo_type, revision, commit_sha, relpath,
size, sha256}` dated at the commit. The cursor carries the whole
SELECTION — `{repo_id, commit_sha, revision, repo_type, allow_patterns,
ignore_patterns}` — so an unchanged sha is an empty pull only when the
repository and filters agree too, and a download matching NO file refuses rather than
cursoring past nothing. Cursors are per mode: pick one mode per
repository, or a `live` pull re-downloads what `backfill` already has.
Pipeline documents then pin the snapshot by its manifest hash (the
transformers pack's `transformers-encode` / `-classify` / `-forecast`,
ADR-0083) — weights enter content-addressed, never by hub name. Install
`dskit[huggingface]`; `libs/huggingface.py` is the knob reference.

## OAuth authorization

OAuth config stores environment-variable names only. For Schwab, export
`SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_CALLBACK_URL`, and
`SCHWAB_TOKEN_PATH`, then run:

```bash
python -m dskit.onboarding authorize --connector schwab --config @source.json
python -m dskit.onboarding authorize --connector schwab --config @source.json \
    --code '<complete callback URL>'
```

The first command prints the browser URL. The second atomically saves a
mode-0600 token; acquisitions refresh it automatically and fail loudly
when manual authorization is required again.

## The root layout

```
onboarding_root/
├── store/                                    # P2 evidence store (onboarding model)
├── raw/<source>/<acq_id>/                    # WORM: payload/ + manifest.json
├── observations/<source>/<acq_id>/<stream>.jsonl[.gz]  # normalized bitemporal rows
├── forecasts/<source>/<acq_id>/<stream>.jsonl[.gz]     # declared forecasts, apart
├── state/<source>/<stream>-<mode>.json       # one cursor per mode
├── state/coverage.sqlite                     # coverage ledger (ADR-0030)
└── published/<dataset>/NNNNNNNN-<hash8>.json # the outbox dskit.assets scans
```

## Compressed storage (ADR-0036)

A source opts into gzip storage through a reserved `storage` block inside
its config object — the connector never sees it:

```jsonc
"config": {
  "storage": {"payload_codec": "gzip", "observations_codec": "gzip"},
  ...connector knobs...
}
```

Both codecs default `"none"`; the codec is declared by the file
EXTENSION (`.jsonl` vs `.jsonl.gz`), never a manifest field, so
manifests, `acq_id`s, and `verify` are untouched (digests cover the
stored bytes). gzip members are written deterministically (`mtime=0`,
no filename, pinned level). Flip `payload_codec` freely — nothing
external reads bronze bytes. Flipping `observations_codec` is a
CONSUMER-VISIBLE change — but consumers that read through the seam
below are codec-blind already.

## Reading observations back (ADR-0037)

Consumers never hand-roll a glob over `observations/`. The read seam:

```python
from dskit.onboarding import scan_stream, stream_digest

records = scan_stream(root, "mysource", "bars",
                      key_fields=("symbol", "ts"),  # the dedup key
                      ts_field="ts",                # -> asof_ms, in place
                      shared_fields=("symbol",))    # heavy-repeat values
digest = stream_digest(records)                     # frozen dump recipe
```

`scan_stream` resolves either codec spelling per acquisition dir,
dedups bitemporally (latest `acquired_at` instant wins; a differing
tie at the winning instant refuses), holds the stream ONCE (the
records are the winning `data` dicts), and is loud on every corrupt or
tamper-shaped store. `stream_digest` fingerprints without ever
building the whole-snapshot string, byte-identical to
`sha256(json.dumps(records, sort_keys=True))`.

An acquired FILE tree (a model, ADR-0082) is read back by CONTENT:

```python
from dskit.onboarding import verified_payload_dir

files_dir = verified_payload_dir(root, manifest_hash, "snapshot")
# the snapshot is located by its manifest hash, re-hashed, and refused by
# name on any drift; load from files_dir with local_files_only=True
```

## Contents

```
dskit/onboarding/
├── __init__.py        public surface: OnboardingRoot, Connector, run_acquisition, ...
├── base.py            reuse from assets (hash, errors) + fsync durability + parse_utc
├── default_model.py   the ratified P2 model as data (hash-pinned to the ADR doc)
├── layout.py          OnboardingRoot: every path in the estate
├── connector.py       Connector ABC, message envelope, check_config, resolve_connector
├── state.py           checkpoint cursors keyed (source, stream, mode)
├── coverage.py        CoverageLedger: the (source, stream, unit, period) done-set
├── leads.py           LeadGrid: capture instants at declared fractions of an expiring life (ADR-0075)
├── codec.py           extension-declared codecs: deterministic gzip, loud decode (ADR-0036)
├── observations.py    the read seam: deduplicated snapshots + content digest (ADR-0037); verified_payload_dir for FILE trees (ADR-0083)
├── oauth.py           OAuth2 manual exchange + atomic owner-only refresh tokens
├── snapshot.py        Merkle manifests, WORM commits, verify, find-by-hash
├── acquire.py         run_acquisition: pull -> snapshot -> evidence -> checkpoint
├── validate.py        ValidationSuite / Rule, the rule engine, run_suite
├── certify.py         certify: the decision over one result (block gate enforced)
├── publish.py         publish_version: pointer manifest into the outbox
├── libs/
│   ├── alpaca.py      Alpaca Market Data stock bars (optional alpaca-py)
│   ├── alpaca_quotes.py  Alpaca NBBO quotes folded to one bid/ask per minute (stdlib HTTP)
│   ├── huggingface.py one hub repository at a pinned commit: FILE + inventory RECORD per file (hub client inside the verbs, ADR-0082)
│   ├── kalshi.py      Kalshi trade-API v2 markets/candles/fee_schedules/orderbooks (stdlib urllib, ADR-0075)
│   ├── localfiles.py  reference connector: CSV/JSONL directories (stdlib)
│   ├── localtables.py parquet / newline-JSON table directories (pyarrow inside verbs, ADR-0076)
│   ├── polymarket.py  Polymarket Gamma events/fee_schedules, CLOB books, pmxt hour archive (hub + pyarrow inside read, ADR-0075)
│   ├── predexon.py    Predexon Kalshi L2 order-book snapshots: paced, retried, cursored per ticker (ADR-0075)
│   ├── restapi.py     declarative REST/JSON connector (stdlib urllib, ADR-0017)
│   └── schwab.py      Schwab closed-minute REST bars + OAuth refresh
├── watch.py           repeated finite acquisitions; first error stops
├── __main__.py        the CLI: python -m dskit.onboarding
├── README.md          this file
└── CLAUDE.md          agent orientation
```

Tests: `python -m pytest tests/onboarding -q` (purity gate, model-hash
parity with the architecture doc, connector conformance, CLI e2e through
`sync-published`).
