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
checkpoint cursors, never an inference from dates.

## The 60-second path

```bash
python -m dskit.onboarding init --root ./onboarding_root   # --backend sqlite/parquet for a tier-2 store
python -m dskit.onboarding register-source vendor --root ./onboarding_root \
    --catalog-source vendor-src --connector localfiles \
    --config '{"path": "./data", "effective_field": "date"}' --activate
python -m dskit.onboarding acquire --root ./onboarding_root \
    --source vendor --stream prices --mode backfill      # WORM snapshot + evidence
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

## Writing a validation suite

A suite is JSON — see
[`examples/onboarding/suite-basic.json`](../../examples/onboarding/suite-basic.json)
for a worked one. Each rule:
`{id, target, rule, kwargs, severity, error_if|warn_if, notes}` —
`target` is a stream; the rule produces a failing count; the threshold
(default `"!= 0"`) gates on it. Built-ins: `not_null`, `unique`,
`accepted_values`, `in_range`, `row_count`, `bitemporal`. Tripped
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
  only after the snapshot is), `SCHEMA`, `LOG`, `ERROR`. Unknown types
  are skipped (forward-compat); unknown keys on known types are refused.
  Heavy imports go INSIDE `read()`.

`libs/localfiles.py` is the worked reference —
`tests/onboarding/test_localfiles.py` drives it through the whole
contract and is the conformance template for yours.

Many APIs need no connector code at all: the `restapi` kind DECLARES a
JSON API in config — streams as endpoint paths, a dot-path to the
records, pagination (`none | cursor | page | offset`), one env-var
credential, optional server-side `since` filtering. See
[`examples/onboarding/source-restapi.json`](../../examples/onboarding/source-restapi.json)
for a worked config and `libs/restapi.py` for the knob reference.

## The root layout

```
onboarding_root/
├── store/                                    # P2 evidence store (onboarding model)
├── raw/<source>/<acq_id>/                    # WORM: payload/ + manifest.json
├── observations/<source>/<acq_id>/<stream>.jsonl   # normalized bitemporal rows
├── forecasts/<source>/<acq_id>/<stream>.jsonl      # declared forecasts, apart
├── state/<source>/<stream>-<mode>.json       # one cursor per mode
└── published/<dataset>/NNNNNNNN-<hash8>.json # the outbox dskit.assets scans
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
├── snapshot.py        Merkle manifests, WORM commits, verify, find-by-hash
├── acquire.py         run_acquisition: pull -> snapshot -> evidence -> checkpoint
├── validate.py        ValidationSuite / Rule, the rule engine, run_suite
├── certify.py         certify: the decision over one result (block gate enforced)
├── publish.py         publish_version: pointer manifest into the outbox
├── libs/
│   ├── localfiles.py  reference connector: CSV/JSONL directories (stdlib)
│   └── restapi.py     declarative REST/JSON connector (stdlib urllib, ADR-0017)
├── __main__.py        the CLI: python -m dskit.onboarding
├── README.md          this file
└── CLAUDE.md          agent orientation
```

Tests: `python -m pytest tests/onboarding -q` (purity gate, model-hash
parity with the architecture doc, connector conformance, CLI e2e through
`sync-published`).
