# Decision Log (Architecture Decision Records)

Mandated by both master specs: *"Maintain formal architecture decision records. No
significant design decision may remain undocumented."*

**Statuses:** `proposed` (drafted, not yet ratified) · `accepted` · `superseded` ·
`open` (a decision is required but not yet made — tracked in
[open-questions.md](open-questions.md)).

Each record: **Context → Decision → Consequences**. Grounded in
[`../agent-master-specifications.md`](../agent-master-specifications.md) and the
existing `dskit.pipeline` engine.

---

## ADR-0001 — `dskit.pipeline` is the "Analytical Execution Framework"

**Status:** accepted

**Context.** Both specs repeatedly name an *"Analytical Execution Framework"* as a
collaborator neither package owns, and both list the same exclusions — pipeline
execution, training, scoring, optimization, forecasting. The `dskit.pipeline`
package already provides exactly this: a venue-agnostic engine that runs a declared
process and emits per-node records/artifacts.

**Decision.** Treat the existing `dskit.pipeline` engine as the Analytical
Execution Framework referenced by the specs. Packages 1 and 2 are **new sibling
subpackages**, not modifications to the engine.

**Consequences.** The engine is the *consumer* of Package 1's governed assets and
the *producer* of the observations Package 1 records. Its identity model (sha256
over canonical JSON) is a candidate to reuse for asset versioning — see
[OQ-5](open-questions.md#oq-5). No engine changes are in scope until an interaction
contract requires them.

---

## ADR-0002 — `DatasetVersion` is created in Package 2, registered in Package 1

**Status:** proposed

**Context.** Package 2 owns *"Dataset Version Creation"* and *"Publication"*;
Package 1 owns the *"Dataset Version Registry"*. Read naively both "own"
`DatasetVersion`, which cannot stand.

**Decision.** Package 2 **creates and publishes** a `DatasetVersion` as the final
step of onboarding (certified, immutable). Package 1 **registers** it as the system
of record — cataloging, indexing, lineage, reuse. The **publication event is the
single handoff**; a `DatasetVersion` is immutable across the boundary.

**Consequences.** Requires a publication→registration contract
([context map §2](context-and-ownership.md#2-end-to-end-data-flow), edge 1) and a
decision on its mechanism ([OQ-2](open-questions.md#oq-2)). A `DatasetVersion`
identity must be assignable **at creation time in P2** and stable through
registration — bearing on [OQ-5](open-questions.md#oq-5).

---

## ADR-0003 — Package 2's registration of a source is operational; Package 1 holds the authoritative catalog

**Status:** accepted (2026-08-21, closes OQ-1)

**Context.** Package 2 owns *"Source Registration"*; Package 1 owns a *"Source
Registry"*. Both plausibly claim `Source`.

**Decision.** Package 2 performs *operational* source registration (credentials,
connector config, scheduling metadata) and references a `Source` **identity owned
by Package 1's catalog**; Package 1 is the reuse-and-lineage authority.

**Consequences.** Unblocks both domain models. P1's default model ships `source`
as an authoritative kind; P2's design will reference it by name.

---

## ADR-0004 — Lineage is layered: onboarding + execution → one global graph

**Status:** proposed

**Context.** Package 2 owns *"Onboarding Lineage"* (`LineageRecord`); Package 1
owns a *"Lineage Registry"* (`LineageRelationship`, "full lineage"); the pipeline
produces execution lineage (which inputs produced which outputs).

**Decision.** Three producers, one authoritative graph. Package 2 emits
**onboarding-phase** lineage (source → snapshot → certified dataset version). The
pipeline emits **execution-phase** lineage (dataset/feature/target → run →
artifact/output). Package 1's Lineage Registry is the **single global graph** that
ingests both and answers end-to-end lineage queries.

**Consequences.** Package 1's lineage model must accept edges asserted by external
producers with provenance stamps. Defines part of both interaction contracts. The
graph is append-only, consistent with *immutable history*.

---

## ADR-0005 — Package 1 observes execution; it never triggers it

**Status:** proposed

**Context.** A stated core principle of Package 1 is *"observe execution rather
than manage execution."* Package 1 owns the Run Observation, Artifact, and Output
registries, yet does not own execution, scheduling, or orchestration.

**Decision.** The pipeline (or a thin adapter around it) **pushes**
`RunObservation`, `Artifact`, and `Output` records into Package 1 at run time.
Package 1 exposes ingestion endpoints only — it holds **no** trigger, schedule, or
orchestration surface.

**Consequences.** The observation contract (context map edge 3) is push-based and
one-way. Keeps Package 1 free of execution concerns and keeps the engine's CLI the
sole run entry point, consistent with the toolkit's *"one command runs every
document"* thesis.

---

## ADR-0006 — Package homes and names

**Status:** proposed

**Context.** The README establishes that new capabilities ship as subpackages
beside `pipeline/` (`dskit/<name>/`), stdlib-neutral at the core tier. The two new
packages need import homes.

**Decision (proposed, low cost to revise).** Package 1 → `dskit/assets/`;
Package 2 → `dskit/onboarding/`. Both follow the toolkit's tier rules (core is
domain-neutral and dependency-light; heavy libraries — PostgreSQL/Parquet drivers,
connectors — are optional extras imported at execution time, not at import).

**Consequences.** Names appear throughout later specs, so fixing them early avoids
churn. Purely nominal; revisit before implementation if a better name emerges.

---

## ADR-0007 — Package 1 is ONE config-driven registry engine, not 13 modules

**Status:** accepted

**Context.** The spec lists 13 registries. Hardcoding them contradicts the
repo's mechanics-in-code/taxonomy-in-config philosophy.

**Decision.** `dskit/assets` is a generic engine: a JSON *asset model* declares
kinds (fields, refs, lifecycle). The spec's registries ship as the built-in
default model — 12 kinds + lineage native in the engine = the 13. Engine
validates structure only (6 JSON types); semantic checks are a declared future
seam. Package 2's kinds later become config, not code.

**Consequences.** Governance is topological (feature requires an entity ref;
target has no feature ref; observations are record-only) and exactly as strong
as the pinned model hash — explicit, auditable config-governance.

---

## ADR-0008 — Pipeline→assets observation is file-based; no imports either way

**Status:** accepted (implements ADR-0005; closes OQ-3)

**Context.** The pipeline's purity gate forbids it importing any dskit sibling.

**Decision.** `python -m dskit.assets ingest-run <run_dir>` reads the completed
run directory (result/resolved/nodes/artifacts) and registers RunObservation,
Output, and Artifact assets plus lineage edges. Idempotent: payloads are pure
functions of run-dir content, so re-ingest reuses everything.

**Consequences.** "Observe execution rather than manage it" — literally. Run-dir
format drift is caught by an end-to-end test that ingests a real run.

---

## ADR-0009 — Asset version identity is a content hash + human alias

**Status:** accepted (closes OQ-5)

**Decision.** `version_id` = sha256 over canonical JSON of
`{kind, payload, refs}` (notes stripped) — the pipeline's exact recipe.
Every kind must declare a required `name` field: the human alias. Same hash ⇒
idempotent re-register = reuse before duplication. Provenance
(`registered_at`, `origin`) sits outside the hash.

---

## ADR-0010 — Standalone package; JSON FileStore behind a Store ABC

**Status:** accepted

**Decision.** `dskit/assets` imports nothing outside stdlib + itself (its own
purity gate mirrors the pipeline's); small mechanics are copied into
`assets/base.py`, with a test asserting hash parity against the pipeline.
Behavior seams are `abc.ABC` + `@abstractmethod` (`Store`); tier-1 storage is
human-diffable JSON files (write-once, atomic, append-only events);
sqlite/postgres/parquet arrive later as tier-2 `libs/` store packs.

**Declared limits.** Single writer per store root; queries are directory scans
(fine to ~10⁴ assets). Both are why `Store` is the seam.

---

## ADR-0011 — Tier-2 store packs are deferred until OQ-4 closes

**Status:** accepted (2026-08-22)

**Context.** The `Store` ABC (ADR-0010) invites sqlite/postgres/parquet packs,
and the spec mandates PostgreSQL + Parquet layouts. But storage topology
([OQ-4](open-questions.md#oq-4)) is open, no consumer hits the FileStore's
declared limits, and Package 2's design will generate the real requirements.

**Decision.** Build no `dskit/assets/libs/` store packs yet. When needed:
sqlite first (stdlib; template for postgres), then parquet, then postgres.

**Consequences.** Tier-1 JSON FileStore is the only backend until OQ-4 closes;
packs get built once, against settled requirements.

---

## ADR-0012 — P2→P1 handoff is a pull scan: the published store IS the outbox

**Status:** accepted (2026-08-22, closes OQ-2)

**Context.** A publication must never be lost; certified data must always
become registered. Research (transactional outbox, maildir, DataHub/Amundsen
ingestion, anti-entropy): notification channels reintroduce the dual-write
problem, and catalogs repair missed events with periodic pull runs anyway.

**Decision.** P2 publishes version manifests into `published/` with maildir
discipline (stage, fsync, atomic rename). P1's registrar **scans** that root
and idempotently registers by content hash. The scan is BOTH delivery and
anti-entropy — one code path. An optional future "nudge" only triggers an
early scan; losing it costs nothing.

**Consequences.** No message infrastructure at tier 1; effectively-once =
at-least-once scan + hash-keyed upsert (ADR-0009 makes dedupe free). Matches
the ADR-0008 file-seam precedent. Ordering is irrelevant: versions are
immutable and independent.

---

## ADR-0013 — P2 reuses the assets engine; connectors use the four-verb contract

**Status:** accepted (2026-08-22, closes OQ-4 at tier 1; tier-2 topology follows ADR-0011)

**Decision.** `dskit/onboarding` imports `dskit.assets` (one-way, stdlib-pure)
and keeps its operational records in a P2-local store governed by
`onboarding-model.json` — kinds as config (ADR-0007), reuse before
duplication. Storage topology at tier 1: **per-package roots** on one
filesystem (P2 owns raw/state/published; P1 owns the catalog store); shared
DB deferred to tier-2 store packs (ADR-0011). Connectors: `Connector` ABC
with `spec/check/discover/read` (Airbyte/Singer consensus), default-deny
config, dict message envelope with `protocol: 1` and skippable unknown
types, platform-persisted opaque per-stream state with checkpoint semantics
("everything before this is durable"). In-process now; the data contract is
subprocess-ready later. The pipeline still imports neither package.

---

## ADR-0014 — Bitemporal storage with first-class acquisition modes

**Status:** accepted (2026-08-22, closes OQ-6)

**Context.** Spec: effective date AND acquisition date; forecasts apart from
observations. Project goal: clear tracking of backfill (pulling history) vs
live (pulling forward), with normalized pulls and saves.

**Decision.** Every record carries `(effective_date, acquired_at)`,
append-only — as-of-X-about-Y queries and ML point-in-time correctness by
construction. **`mode: backfill | live` is a declared field** on every
acquisition job, stamped into snapshot manifests and published versions —
tracking is a query, never date arithmetic. Checkpoints keyed
`(source, stream, mode)` so the two cursors never interfere. Snapshots are
WORM: `raw/<source>/<acq_id>/payload/` + Merkle manifest (identity = hash of
canonical manifest). `observations/` asserts `effective_date <= acquired_at`;
`forecasts/` is a separate root. OQ-6: ACQUIRED forecasts live in P2's
forecast root; COMPUTED forecasts remain P1 outputs via `ingest-run`.

---

## ADR-0015 — Validation is declarative; certification consumes results, never data

**Status:** accepted (2026-08-22)

**Decision.** Suites are JSON: rules `{id, target, rule, kwargs, severity,
warn_if/error_if}` with dbt-style thresholds on failing counts (warn never
blocks). Results are content-addressed artifacts
`{suite_hash, snapshot_id, gating: pass|warn|block, statistics, results[]}`.
A certification records a decision over ONE result (`certified | refused` —
a refusal is also evidence); publication requires a certificate. A block is
a result, not an error — the pipeline's NO-GO philosophy, applied to data.

---

## ADR-0016 — P2 is entity-free; entity association is asserted P1-side

**Status:** accepted (2026-08-22, closes OQ-7)

**Context.** P1 has an `Entity` registry and asserts *"features belong to
entities"*; P2's spec has no entity concept — it publishes datasets.

**Decision.** `dskit/onboarding` carries no entity concept. A
`dataset_version → entity` association is asserted **in P1 after
registration** (a ref in the catalog's model), keeping entity resolution a
catalog concern.

**Consequences.** Feature governance is enforceable entirely inside P1's
asset model; publication manifests need no entity fields, so P2's domain
model can freeze now.

---

## ADR-0017 — `restapi`: a declarative REST connector pack (tier-2, stdlib)

**Status:** accepted (2026-08-24)

**Context.** The four-verb contract (ADR-0013) was proven only by
`localfiles`; a network connector is the untested seam. Generic REST
APIs share one shape: endpoints, JSON bodies, pagination, a credential.

**Decision.** `libs/restapi.py`, kind `restapi` (named to avoid stdlib
`http` shadowing), **stdlib `urllib` only** — the pack stays
dependency-free like the rest of tier 1/2. Streams are DECLARED in
config (the Airbyte low-code idea shrunk to this contract): per-stream
`path`/`params`/`records_path`; pagination a closed vocabulary
`none | cursor | page | offset`; ONE credential — a secret knob holding
the env-var name, injected as a declared header or query param via a
format template; `since_param` passes the cursor server-side while the
client-side filter still applies (an over-returning server is harmless).
Pages buffer per stream and sort by effective date so the checkpoint
stays honest (the localfiles ruling). All requests go through one
`_fetch` seam — retries/backoff on 429/5xx/network sit above it; tests
script it, no network, no mock library. Error messages strip query
strings so a param-carried credential can never leak.

**Consequences.** `check_config` now exempts a document-level `notes`
key (the repo's comment standard) — found because the shipped
`source-localfiles.json` example was failing its own default-deny; the
example also drops its redundant `name` (the CLI argument carries it).
`notes` still enters the `source_config` payload hash: source configs
are operational records, not identity-hashed documents like suites.

---

## ADR-0018 — Store backend is declared in `store.json`; sqlite is the first pack

**Status:** accepted (2026-08-24, executes the ADR-0011 deferral)

**Context.** Package 2 settled the requirements ADR-0011 waited for.
`FileStore` is constructed at three hardcoded sites (assets CLI,
`OnboardingRoot`), so a pack alone would be unreachable; and postgres
later has no filesystem layout, so detection-by-contents can't be the
seam.

**Decision.** A store root **declares its backend in `store.json`**:
optional `"backend"` key, absent = `"file"` — every existing root is
untouched. Core grows `open_store(root)` / `create_store(root, model,
backend)`; vocabulary follows the connector precedent (ADR-0013):
built-in names (`file`, `sqlite`) or `pkg.module:Class` for tier-3
stores, unknown refused loudly. Packs import lazily inside the factory.
`FileStore` refuses a root declaring another backend. First pack:
`libs/sqlite.py` — `store.json` keeps the pin, `store.sqlite` holds
`records(version_id PK, kind, body)` + kind index and
`events(seq AUTOINCREMENT, body)`; bodies are the same canonical JSON,
same rehash-on-read tamper check. WAL, `synchronous=FULL` (durability
parity with the fsync discipline), busy timeout, connection per call —
lifting BOTH declared FileStore limits (single writer, scan queries).
`import sqlite3` stays inside methods: stdlib, but the pack is the
template for postgres, whose driver import must be lazy. Core also
gains `copy_store(src, dst)`: any-to-any replay, matching pins, empty
destination.

**Consequences.** The store conformance battery parametrizes over
backends — every future pack passes the identical suite. The purity
gate sanctions exactly `libs/` and extends its static scan to it.
`OnboardingRoot.create` and both CLIs take a backend choice; opening
goes through `open_store` everywhere.

*Amended after adversarial review (2026-08-24).* Three contract
clarifications, pinned by the battery: (1) **concurrency is lifted at
the Store seam only** — single calls are atomic/durable under
concurrent writers, but Registry/Lineage check-then-act mutation still
assumes one mutating writer per root (a proven lineage-cycle race
otherwise corrupts the append-only log irreparably; engine-level
coordination would be its own ADR). (2) `iter_events` is a **snapshot**
on every backend. (3) `create` refuses a root holding ANY store
artifact — a crashed create, whichever backend's, is deleted and
redone, never built over. Backend guards accept whatever class the
declared reference resolves to, so `pkg.module:Class` refs to builtins
round-trip; raw `sqlite3` exceptions never cross the seam.

---

## ADR-0019 — `parquet`: an analytics-interop store pack (per-record files)

**Status:** accepted (2026-08-24)

**Context.** The spec mandates a Parquet storage layout (OQ-4 closed at
tier 1 by ADR-0013; packs by ADR-0018). Parquet files are immutable —
no append, no locking — so this pack cannot lift concurrency; its value
is different: the store becomes directly scannable by any parquet
engine (duckdb, polars, spark) without going through the Python API.

**Decision.** `libs/parquet.py` (`ParquetStore`, built-in name
`parquet`, extra `pyarrow>=15`, imports inside methods). Layout:
`store.json` keeps the pin and is written LAST (the commit point);
`records/<kind>/<version_id>.parquet` one row per file, write-once via
tmp + fsync + `os.replace`; `events/<seq:08d>.parquet` one row per
event, append = max+1. Plain `<kind>/` dirs, NOT hive `kind=` names —
`kind` is a real column in every file and hive dir names collide with
it in duckdb; `records/*/*.parquet` glob-scans the whole dataset.
Schema is the sqlite body idiom: `(version_id, kind, body)` /
`(seq, body)`, body = canonical JSON string, rehash-on-read tamper
check. `iter_events` snapshot = the sorted file list captured at call
time. pyarrow and `OSError` failures cross the seam as `AssetError`.

**Declared limits.** Single mutating writer per root (the max+1 seq is
check-then-act, like FileStore's append); queries are directory scans.
The pack lifts analytics interop only — concurrency stays sqlite's.

**Consequences.** `_BACKENDS` gains `parquet`; `_STORE_ARTIFACTS`
gains `events` (the directory), so create-refusal covers parquet
leftovers. The conformance battery adds the backend behind an
importorskip guard — the suite passes with pyarrow absent.
