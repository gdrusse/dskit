# Package 2 Design — `dskit/onboarding` (Acquisition & Onboarding)

*Status: **draft for ratification**. Grounded in the
[master spec](../agent-master-specifications.md), ADR-0012…0015, and open
research (Airbyte/Singer/dlt connector contracts; outbox/maildir/catalog
handoff patterns; bitemporal modeling, Delta/Iceberg/DVC snapshot layouts,
GE/dbt validation design).*

## The one-paragraph model

`dskit/onboarding` is connectors + validation + publication on top of the
**assets engine reused as a library** (ADR-0013): its operational records
(source configs, acquisition jobs, snapshots, validation results,
certifications, published versions) are asset records in a P2-local store
governed by [onboarding-model.json](onboarding-model.json) — kinds as
config, per ADR-0007. Data flows: a connector **pulls** (backfill or
live), every pull lands as an **immutable WORM snapshot** with a
Merkle-style manifest, a **declarative validation suite** produces a
content-addressed result, a **certification** consumes that result, and
publication writes a **version manifest** into a published root that
Package 1 **scans and registers** (pull, ADR-0012). Every record carries
`(effective_date, acquired_at)` — the bitemporal pair (ADR-0014).

## Acquisition modes — first-class (a stated project goal)

Tracking *what is pulled historically* vs *what is pulled forward* is a
declared field, never an inference:

- Every `acquisition_job` declares **`mode: "backfill" | "live"`**;
  the mode is stamped into the snapshot manifest and its records.
- **Backfill** pulls history: `effective_range` lies in the past,
  `effective_date << acquired_at`. **Live** pulls the present:
  `effective_date ≈ acquired_at`.
- Checkpoints are keyed **`(source, stream, mode)`** — the backfill
  cursor walks backward independently of the live cursor walking
  forward; neither can corrupt the other.
- Both modes flow through the SAME connector contract, envelope, and
  snapshot layout — normalization is structural. "What did we know as of
  X about date Y" is answered by filtering `acquired_at <= X` and taking
  the latest acquisition per `effective_date`.

## Connector contract (ADR-0013)

`Connector` ABC, four verbs — the Airbyte/Singer consensus, repo-idiom:

| Verb | Does | Cost |
|---|---|---|
| `spec()` | declare allowed config knobs (default-deny) + which are secrets | import-cheap |
| `check(config)` | fail fast: can we connect? | network, no data |
| `discover(config)` | streams + schemas + primary keys | cheap |
| `read(config, streams, state, mode)` | yield message dicts | heavy imports live HERE |

Messages are plain dicts `{"protocol": 1, "type": "RECORD" | "STATE" |
"SCHEMA" | "LOG" | "ERROR", ...}`; unknown types are skippable (the
forward-compat valve). A `STATE` message means *everything before this is
durable* (Fivetran semantics); the platform persists state as JSON, its
content opaque to the platform. Delivery is at-least-once + idempotent
save — effectively-once, same reasoning as ADR-0012. Import = registration
(as with pipeline nodes); in-process now, subprocess-ready later because
the contract is data, not an ABI.

## Storage layout (ADR-0014)

```
onboarding_root/
├── store/                      # the P2 assets store (onboarding model)
├── raw/<source>/<acq_id>/      # WORM snapshot: bronze, exactly as received
│   ├── payload/...             # untouched files
│   └── manifest.json           # mode, effective_range, acquired_at,
│                               #   files: [{relpath, sha256, size}] — Merkle root
├── observations/               # normalized records, effective_date <= acquired_at ASSERTED
├── forecasts/                  # acquired forecasts, segregated (spec rule; OQ-6)
├── state/<source>/<stream>-<mode>.json    # checkpoint cursors
└── published/<dataset>/        # the outbox P1 scans (ADR-0012)
    └── NNNNNNNN-<hash8>.json   # version manifest: pointers to certified
                                #   snapshot hashes — never data copies
```

Writers stage + fsync + `os.replace` (maildir discipline); nothing under
`raw/` or `published/` is ever modified; a `verify` command re-hashes
against manifests (tamper evidence, DVC-style).

## Validation & certification (ADR-0015)

Suites are JSON documents; rules are
`{id, target, rule, kwargs, severity, warn_if/error_if}` with thresholds
on failing-row counts (dbt model: warn never blocks). Results are
content-addressed artifacts `{suite_hash, snapshot_id, gating:
pass|warn|block, statistics, results[]}`. **Certification consumes the
result artifact, never the data**; publication requires a certificate.
Gate semantics mirror the pipeline: a block is a result, not an error.

## Publication → registration (ADR-0012, closes OQ-2)

The published root IS the outbox. P1's registrar
(`sync-published <root>`) scans it and idempotently registers
`dataset_version` records + onboarding-phase lineage
(source → snapshot → version) into the P1 store, keyed by content hash —
the scan is simultaneously delivery and anti-entropy. An optional future
"nudge" only triggers an early scan; losing it costs nothing.

## Spec deliverables map

| Spec deliverable | Where |
|---|---|
| 1 Domain model | [onboarding-model.json](onboarding-model.json) |
| 2 Lifecycles | model document (`source_config`; records are record-only evidence) |
| 3 Storage & versioning | layout above + ADR-0014 |
| 4 Package interaction | ADR-0012 + [context map](context-and-ownership.md) |
| 5 Connector framework | ADR-0013 + contract above |
| 6 Validation framework | ADR-0015 |
| 7 Security | secrets flagged in `spec()`, kept in env/config outside hash-material; full spec deferred to build |
| 8 Testing | mirror of P1's: purity gate, conformance suite for connectors, e2e acquire→publish→sync |
| 9 Monitoring | job/snapshot records + event log ARE the observability surface at tier 1 |
| 10 Deployment | single machine, per-package roots (OQ-4); tier-2 store packs later (ADR-0011) |

## Declared non-goals (tier 1)

No schedulers/daemons (jobs are invoked), no multi-writer roots, no
subprocess isolation yet, no feature/target joins ever (spec join policy).
