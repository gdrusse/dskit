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
check. `iter_events` snapshot = the sorted file list captured when
iteration begins (the ABC's wording; parity with both siblings).
pyarrow and `OSError` failures cross the seam as `AssetError` — the
driver is probed at open AND create, so a root never yields an
instance whose data calls would leak ImportError. Foreign entries are
refused loudly, extension-blind: anything in `events/` or
`records/`(`<kind>/`) the API cannot account for — wrong name, wrong
suffix, or a directory where a record/event file belongs — is one an
engine scan would treat differently, and the two read paths must
never silently disagree. Names with the engine-ignored `.`/`_`
prefixes (AppleDouble sidecars, crashed temps) are invisible to both
paths and exempt.

**Declared limits.** Single mutating writer per root (the max+1 seq is
check-then-act, like FileStore's append); queries are directory scans.
The pack lifts analytics interop only — concurrency stays sqlite's.

**Consequences.** `_BACKENDS` gains `parquet`; `_STORE_ARTIFACTS`
gains `events` (the directory), so create-refusal covers parquet
leftovers. The conformance battery adds the backend behind an
importorskip guard — the suite passes with pyarrow absent.

---

## ADR-0020 — Store integrity parity: key trust, loud foreign entries, wrapped I/O

**Status:** accepted (2026-08-24)

**Context.** The ADR-0019 review rounds surfaced integrity gaps the
parquet pack fixed for itself but that are contract-level, and the
TODO register held four deferred loud-not-silent items. Closing them
together keeps the three backends at ONE standard.

**Decision.** (1) **Storage-key trust, both axes:** every content
read that answers for a key — `get_record`, and `put_record`'s
verify-on-duplicate — checks `loaded.version_id() == key` AND that
the record's declared kind matches its storage location (directory or
kind column) on every backend; a valid foreign record planted under
another key OR another kind is refused, never returned. A vid listed
under two kinds proves a plant (the kind is inside the hash) and
enumeration refuses it. Point lookups (`_find`) refuse a
key-conforming path that is not a regular file, and skip the same
`.`/`_` prefixed kind entries enumeration skips (the symmetric half
of the doctrine; a non-key-conforming foreign FILE still surfaces
only through enumeration — point lookups probe exact keys). A
directory squatting `events.jsonl` is refused on read, never
mistaken for an empty history. (2) **FileStore adopts the foreign-entry
doctrine** (ADR-0019): `.`/`_`-prefixed names ignored, anything else
unaccountable in `records/` refused loudly — never a garbage stem
that detonates downstream — and ALL its runtime I/O failures cross
the seam as `AssetError` (the packs' standard). (3) **Identifier
regexes are `\Z`-anchored** (`_SEGMENT`, `_VERSION_ID`, and
onboarding's twin `_SEGMENT`, whose comment binds it to the assets
rule; the pipeline's `_SEGMENT_OK` is out of scope — separate
package, no shared-rule claim): `$` forgives a trailing newline.
(4) **Sqlite runtime connects use URI
`mode=rw`**, so a call against a damaged root fails loudly instead of
recreating a stray empty database. (5) The assets purity gate
resolves relative-import LEVELS (a `from ...pipeline` escape was
mapped to the package itself); the pipeline gate already did.

**Consequences.** Tier-1 behavior change: a FileStore root holding
stray non-record files now refuses enumeration loudly (dotfiles and
`_`-prefixed names stay invisible). Battery grows key-trust,
verify-on-duplicate, and newline-identifier coverage on all three
backends. Engine-level multi-writer coordination remains the one
deferred TODO item — no consumer needs it (ADR-0011's discipline).

---

## ADR-0021 — Child projects: the `children/` incubation convention

**Status:** accepted (2026-08-25)

**Context.** dskit is the cornerstone of the owner's DS work: a project
must never modify dskit — it consumes it as a thin **child** (tier-3
wrappers + JSON configs over onboarding/assets/pipeline). A child needs
a place to grow before it earns its own repository, and every child
should share one canonical shape so the pattern stays teachable.

**Decision.** (1) **`children/<project>/` at the repo root incubates a
child.** It is never imported by dskit (packaging already ships only
`dskit*`; an isolation test additionally asserts no `dskit/` module
references `children`), never on `sys.path` for the toolkit, and not
part of the distribution. (2) **A child is laid out exactly as its
future standalone repo** — own `pyproject.toml` (depending on `dskit`),
one package directory of tier-3 modules, `configs/`, `tests/` — and
**graduates unchanged**: nothing inside may reference its incubation
position (no `..` imports, no dskit-repo paths; the only coupling is
`import dskit`). (3) **This repo's suite runs child tests by
subprocess**: `tests/children/test_children.py` enumerates
`children/*/` (skipping `_`-prefixed entries, the store's
foreign-entry idiom) and runs each child's own pytest; a red child
fails the dskit suite. Each child's `tests/conftest.py` bootstraps
`sys.path` to its own root, so the same tests run before and after
graduation without installing the child. (4) **`children/_skeleton/`
is the pinned canonical shape** — not prose but a RUNNABLE child
exercising all three seams (a connector, registered node kinds under
conformance, an asset model + run configs validated by the engines).
Its file list is pinned in `tests/children/test_skeleton.py`; changing
the skeleton's shape means updating the pin in the same commit,
deliberately. (5) **The guide is `children/README.md`**: copy the
skeleton, rename, obey the rules — never edit dskit; a missing
capability is either an ADR'd generic gap in dskit or stays in the
child; the domain lives in configs — then graduate by the checklist.
(6) `children/*/tests/**` joins the `tests/**` ruff per-file-ignores
(same importorskip idiom).

**Consequences.** The repo gains `children/` and `tests/children/`;
wheels are unchanged. Capability-gap work on external projects lands
as child sketches/incubations, never as dskit code. The skeleton is
continuously verified against engine evolution by the suite itself.

---

## ADR-0022 — Flow-verb parity: port `concat` / `join` / `derive` from the parent engine

**Status:** accepted (2026-08-25)

**Context.** `dskit/pipeline` was extracted from the pmquant engine;
the parent's node registry is a strict superset. Three flow verbs did
not make the extraction: `concat` (merge record streams), `join`
(attach keyed lookup rows to records), `derive` (compute fields per
record). All three are tier-1 generic — the parent's own purity gate
forbids venue names in them — and any multi-source document needs them
(two sources → one stream is `concat`; a rate table onto records is
`join` + `table-file`; a computed field is `derive`). Their absence is
exactly the "missed a verb" failure the parent's registry doctrine
warns about.

**Decision.** Port the three kinds into `dskit/pipeline/kinds_flow.py`
faithfully from the parent (rename only), register them beside the
existing four, and port their tests + toolkit-conformance probes.
Anything in the parent implementation that depends on seams dskit does
not have (event-bounds / split-policy, ADR-0024's subject) is stripped
and recorded in the port notes — never half-ported.

**Consequences.** `DEFAULT_NODE_KINDS` grows 8 → 11. `kinds_flow.py`
grows ~1,000 lines with matching test growth. Future parent↔child
diffs shrink.

---

## ADR-0023 — Table kinds: port `table-file` / `table-write`

**Status:** accepted (2026-08-25)

**Context.** Same extraction gap as ADR-0022. The parent ships
`kinds_table.py`: `table-file` loads a digest-verified keyed table
from JSON (config declares path + sha256; load refuses drift) and
`table-write` writes one atomically without clobbering — a provenance
round-trip pair. The module's own docstring disclaims all domain
knowledge; it is the generic answer to "my document needs a versioned
lookup table" (fee schedules, symbol maps, thresholds) without smuggling
data into params.

**Decision.** Port `kinds_table.py` as a new tier-1 module, register
`table-file` / `table-write` in `DEFAULT_NODE_KINDS`, port its tests
and conformance probes. Same strip-and-record rule as ADR-0022 for any
absent-seam references.

**Consequences.** `DEFAULT_NODE_KINDS` grows 11 → 13. New module
`dskit/pipeline/kinds_table.py` (~400 LOC) + `tests/pipeline/
test_kinds_table.py`. Documents gain a sanctioned data-beside-config
mechanism.

---

## ADR-0024 — Split-assignment policies + event bounds

**Status:** accepted (2026-08-25 — proposed and owner-ratified same day)

**Context.** The parent engine assigns split membership through a
declared POLICY — `record` (the record's own instant), `event-open`,
`event-close` — so a multi-record event straddling a cut cannot leak
across train/val. Machinery: `split_policy.py` (policy registry),
`EventBounds` + `merge_event_bounds` in base, `Node.event_bounds()`
(the per-event companion to the shared `data_edge()`), and a driver
binding step that asks data nodes for bounds whenever the materialized
split declares an event policy — refusing loudly when none supplies
them. All of it is generic leakage-guard machinery (the motivating bug
was domain, the mechanism is not).

**Decision.** Port faithfully: `split_policy.py` as a new tier-1
module, the base/node/driver hooks per the parent diff (~190 base
lines, ~30 node lines, ~70 driver lines), tests included. Engine-core
surgery across three load-bearing files — proposed first for exactly
that reason; ratified by the owner.

**Consequences.** Splits gain a `policy` knob; documents with
multi-record events get a principled leakage guard; the parent↔child
engine diff shrinks to prose. Unblocks migrating the parent's adapter
onto this engine.

---

## ADR-0025 — Declared-model seam: config-named library classes

**Status:** accepted (2026-08-25 — proposed and owner-ratified same day)

**Context.** The parent completes the config doctrine for deep
learning: `base.py` gains `library_path_problems` / `import_library_class`
(plan-time validation of a "name me a class from some library" param),
the torch pack gains `torch-train` / `torch-predict` (`DeclaredTrain` /
`DeclaredPredict` — the DOCUMENT names the `nn.Module`), transformers
gains `transformers-fit`, and `trainlog.py` records per-epoch
`TrainingCurve` + probability metrics (logloss/brier/ECE reusing the
metrics module). Today dskit's torch/transformers packs require a
subclass per model — code where the doctrine says config.

**Decision.** Port the seam + the three kinds + `trainlog` faithfully
(torch pack ~665 → ~1,384 lines; transformers +194; trainlog ~300 +
tests). Ratified by the owner.

**Consequences.** Model swaps become config edits; sklearn (already
declared via `estimator`) and torch reach parity. The torch and
transformers packs' `NODE_KINDS` grow by three (`torch-train`,
`torch-predict`, `transformers-fit`); `DEFAULT_NODE_KINDS` is
unchanged — pack kinds register adapter-side or resolve by import
path, like every libs kind.

(2026-08-25: a parallel bespoke implementation of this ADR landed on
main and was superseded on merge by the faithful parent port this entry
specifies; its walk-forward/sb3/matplotlib/coverage siblings —
ADR-0027..0030 — are kept in full.)

---

## ADR-0026 — Report renderer parity

**Status:** accepted (2026-08-25 — proposed, then owner-ratified with
the rest of the parity ports)

**Context.** The parent's `run-report` renders what dskit's cannot:
CSV export beside the markdown, bounded tables (`max_rows`/`skip` with
explicit truncation notes — silent truncation reads as coverage), and
ledger/rate table helpers (~1,090 extra lines). Most is generic
rendering; two helpers (trade rows, per-instrument hit rate) sit at
the trading-genre boundary.

**Decision.** Port the whole module faithfully — the boundary
question resolves to "take the ledger/hit-rate helpers too": this
toolkit already ships position-taking primitives at tier 1
(`records.py`: MarketRecord, binary/mark-to-market accounting,
`settle_position`), so rendering the ledgers those primitives produce
is in-genre, and the parent's purity gate holds the helpers
venue-free. A non-trading project simply never feeds them. Faithful
whole-module ports have also proven safer than surgical partial ones
four times running (ADR-0022…0025).

**Consequences.** Evidence reports become spreadsheet-consumable and
honestly bounded; `kinds_report.py` grows ~409 → ~1,500 lines with
matching test growth; `run-report` stays owned and its param surface
grows (bounded-table knobs).

---

## ADR-0027 — Walk-forward evaluation: `walkforward` section + embargoed time splits

**Status:** accepted (2026-08-25) — owner directive: ensure the generic
bones for rl_stocks (`child-gap-rl-stocks.md`)

**Context.** The rl_stocks re-inventory shows its alpha engine's whole
validation methodology is rolling-origin evaluation: K fold cutoffs, an
expanding train window, a structural embargo (train ends a horizon
before validation starts, so unresolved labels cannot leak), per-fold
fresh training, and an aggregate gate over per-fold scores. dskit's
splits are single-cut families, and the search seam overrides node
PARAMS only — time is not reachable, so a document cannot declare this
experiment shape. The mechanism is generic leakage-aware evaluation for
any autocorrelated panel; only the motivating labels are domain.

**Decision.** Two additive pieces. (1) `TimeSplitConfig` gains optional
`val_start_ms`: records in `(train_end_ms, val_start_ms)` belong to NO
split (`split_of` → `None` — the embargo band); `TrailingSplitSpec`
gains `embargo_days`, materializing that band. Absent, semantics are
unchanged. (2) A top-level `walkforward` document section — folds (an
explicit cutoff list or `{first, step_days, count}`), `val_days`,
`embargo_days`, `objective` (a `$node.path` into a score output),
`select` — hashed as identity, plus `run_walk_forward` in the driver
and a CLI verb: one derived document per fold (splits replaced by that
fold's pinned cuts), each through `run_document` — a full run dir per
fold — and an aggregate summary (per-fold scores, mean/std, states). A
fold that halts is recorded, not fatal; a fold that errors aborts.

**Consequences.** Walk-forward becomes a declared experiment, not a
script; every fold is an ordinary reproducible run. The rerun seam is
untouched. Train windows are expanding ("all-prior") in v1 — bounded
train joins the I-223 restriction. (2026-08-25 merge note: ADR-0024
landed in the same reconciliation, so the per-event policy machinery
now ships — but fold splits still carry no policy; a declared event
policy alongside `walkforward` refuses loudly rather than silently
running folds under `record`. Policy pass-through into folds is
future work (closed by ADR-0031).)

---

## ADR-0028 — `sb3`: declared RL training pack (tier 2)

**Status:** accepted (2026-08-25) — owner directive, as above

**Context.** rl_stocks' RL layer is stable-baselines3 + gymnasium end
to end — `SAC("MultiInputPolicy", env).learn().save()` with a
hand-written save path, no artifact protocol, no eval cadence; the env
is a domain simulator behind the gymnasium API. Everything there but
the env is generic RL plumbing any sequential-decision project
rewrites. The torch pack already answers "the document names the
model" for supervised learning; RL has no doorway.

**Decision.** A tier-2 pack `libs/sb3.py`: `sb3-train` (role `train`)
— the DOCUMENT names the algorithm (resolved from stable_baselines3 by
name at run), the policy, and the ENVIRONMENT class by import path
(`env: "pkg.module:Class"` + `env_params` handed to its constructor
verbatim, the pyomo `solver_options` precedent), trains
`total_timesteps` under a recorded seed, and saves SB3's model.zip
plus a hash-pinned sidecar (the torch pack's artifact discipline).
`sb3-policy` (role `signal`) restores a pinned artifact into an
`act(obs)` signal, refusing mismatches by name. `sb3-eval` (role
`score`) runs `evaluate_policy` over the declared env for n episodes.
Heavy imports inside `run()`; nothing registers on import.

**Consequences.** An RL project writes its env (domain) and a config;
training/eval/artifact plumbing stops being child code. Determinism is
best-effort per SB3's own guarantees — recorded, never promised.

---

## ADR-0029 — `matplotlib`: declared-figure report pack (tier 2)

**Status:** accepted (2026-08-25) — owner directive, as above

**Context.** Both children hand-roll chart rendering: rl_stocks
maintains six evaluation figures TWICE (matplotlib for PNG, plotly for
HTML, aligned only by discipline) plus five more in its alpha engine;
pmquant's reports do the same dance. The pattern is always "plot named
numeric fields of a row stream"; nothing about it is domain.

**Decision.** A tier-2 pack `libs/matplotlib.py`: `mpl-figure` (role
`report`) renders a declared list of `marks` (`line | scatter | bar |
hist` over named fields of the `rows` input) to a PNG artifact under
the run dir — title/axis labels/dpi as default-deny params, Agg
backend, matplotlib imported inside `run()`. An abstract `FigureNode`
base (hook `build_figure`) carries bespoke figures the declaration
cannot express.

**Consequences.** Standard evidence charts become config; bespoke
charts become small subclasses instead of parallel rendering stacks.
HTML/plotly parity is explicitly out of scope (a second pack if a
child ever justifies it).

---

## ADR-0030 — Onboarding coverage ledger

**Status:** accepted (2026-08-25) — owner directive, as above

**Context.** Sparse backfills need a finer primitive than the
per-(source, stream, mode) watermark cursor: rl_stocks' Layer 1 is
built around a SQLite "fetch manifest" — a per-(source, ticker, day)
done-set with `fetched`/`no_data` tombstones, gap queries driving what
to pull next, cadence-based staleness for slow-moving sources,
reconcile-from-disk, and a drift audit. pmquant's gap report flagged
the same need below the line ("which-days-pulled fetch manifest"). Two
children now demand it; nothing in it is domain — units and periods
are opaque strings.

**Decision.** A tier-1 module `dskit/onboarding/coverage.py`:
`CoverageLedger`, SQLite-backed (stdlib, imported lazily — the assets
sqlite-pack template) at a root-owned path, keyed
`(source, stream, unit, period)` with a closed status vocabulary
(`fetched` | `no_data`). Queries: `missing(units, periods)` — the
caller DECLARES the expected periods, the toolkit never guesses a
calendar; `stale_units(units, cutoff)`; `covered(unit)`. Mutations:
`mark`, `clear`. Truth checks: `audit(observed)` — the symmetric
ledger-vs-reality diff — and `reconcile(observed)` to adopt store
truth. Single-writer per root (the store-seam doctrine). Acquisition
does not consult it implicitly — the backfill loop drives it.

**Consequences.** Backfill idempotency becomes a platform primitive
with an honest blind-spot story: expected periods are declared, never
inferred, so the rl_stocks observation-range-inference bug class is
unrepresentable. The onboarding root grows one state file; `verify`
ignores it (state, not evidence).

---

## ADR-0031 — Walk-forward folds carry the document's split policy

**Status:** accepted (2026-08-25 — owner directive to close the
deferred residuals)

**Context.** The ADR-0027 merge note left a seam closed but blunt: a
document declaring a split `policy` alongside `walkforward` refuses
loudly, because `_fold_splits` builds each fold's `TimeSplitConfig`
without a policy and silently running folds under `record` was the
fallback ADR-0024 forswears. Refusal beats leakage, but the combination
is exactly what an event-shaped walk-forward wants: fold cuts AND
event-atomic assignment.

**Decision.** Pass the declared policy through: `_fold_splits` stamps
the parent document's `splits.policy` onto every fold's
`TimeSplitConfig`. Each fold already routes through `run_document`, so
event policies get the ADR-0024 binding — bounds from the fold's data
nodes, loud refusal when none supplies them — and the `val_start_ms`
embargo band keeps applying to the policy-selected instant. The
walkforward-door refusal guard is removed; its test becomes the
pass-through test. A document with no `splits` section (or `record`)
behaves exactly as before — hash-neutral, behavior-neutral.

**Consequences.** `run_walk_forward` loses the guard, `_fold_splits`
gains one stamped field; the ADR-0027 merge note's "future work" is
closed. Fold-level event policies are now first-class.

---

## ADR-0032 — The child is the adapter unit; `pipeline_<venue>` is retired

**Status:** accepted (2026-08-25 — owner-ratified)

**Context.** The extraction inherited the parent's adapter naming:
prose, CLI help, and error messages across the pipeline package pointed
at `dskit.pipeline_<venue>` sibling packages. That prefix was a
monorepo artifact — inside the parent it distinguished the adapter from
its sibling engine within one distribution. Post-ADR-0021 it misleads
twice over: it suggests creating a venue package INSIDE dskit
(violating the toolkit's first law), and it brands one seam when a real
project adapts all three pillars (nodes + connectors + asset model),
not "the pipeline".

**Decision.** The CHILD is the adapter unit. All project/venue
adaptation — node kinds, connectors, store backends, backend tags,
tracking sinks, asset models — lives in that project's ONE child
package (`children/<project>/` while incubating, its own repo after;
ADR-0021). There is no `pipeline_<venue>` package, in dskit or beside
it. A genuine venue split inside a project (e.g. per-venue accounting)
is a MODULE of the child (`nodes_<venue>.py`), never a package
taxonomy. Every `dskit.pipeline_<venue>` exemplar in docstrings, CLI
help, and error messages is replaced with the child form
(`--adapter yourproject`; `yourproject.nodes:Class`); migration
guidance for the parent's `pipeline_kalshi`/`pipeline_schwab` lands
their content as child modules — the package names do not survive.

**Consequences.** Eleven in-code references swept (help strings,
runtime errors, docstrings, both purity-gate messages, two help-pinning
tests); `children/README.md`, both READMEs, the pipeline CLAUDE.md, the
pmquant gap report, and RE-ENTRY state the ruling. Nothing behavioral
changes — `--adapter` and class-path resolution were always
name-agnostic.

---

## ADR-0033 — stat_test self-description, a studentized method, a correction registry

**Status:** accepted (2026-08-26 — owner graduated pmquant §13 items 1–3)

**Context.** Three generic gaps in the stats seam. The report renderer
already reads `totals["test"]/["independence_unit"]/["n_boot"]/["seed"]`
and probes per-instrument `ci_low`/`ci_high`, but `StatTest` never emits
them — the rendered edge section describes the cluster bootstrap by
fallback string regardless of what ran. The plain bootstrap is not
gate-grade for a deploy verdict that authorizes sizing inside one
document (pmquant TODO-2, the structural blocker). Corrections are a
closed dict `{bh, bonferroni, none}`; weighted BH is unregistrable.

**Decision.** (1) *Self-description*: `evidence.totals` gains `test`,
`statistic`, `independence_unit`, `n_boot`, `seed`, `method` for both
methods; studentized rows carry `se` always and `t`/`ci_low`/`ci_high`
only when finite — omitted, never null/NaN. The plain method's `test`
string equals the renderer's existing fallback, so old runs render
unchanged. (2) *A `method` param* on the `stat_test` kind —
`METHODS = ("plain", "studentized")`, a **closed tuple**, not a
registry: the statistic is the ruler and stays owned (and the role stays
unsearchable, so `method` is not HPO-addressable). `"studentized"` is a
one-sided studentized recentered cluster bootstrap-t: size-weighted
pooled mean, cluster-robust linearized SE (`s/√n` in the single-record
degenerate), recentered pivot with signed conventions for degenerate
replicates, add-one p, two-sided bootstrap-t CI — descriptive,
uncorrected for the family. The default `"plain"` is byte-stable (same
seed stream, same p-values). (3) *A correction registry*: `CORRECTIONS`
reshaped to the split-policy metadata pattern
(`name -> {fn, needs_weights, doc}`) with `register_correction`
(duplicates raise) and a loud lookup; `weighted-bh` ships
(Genovese–Roeder `p/w` with raw weights, `needs_weights=True`). Weights
reach the node as an **input port** — family membership and weights are
data, not config. The stage-list grammar refuses `needs_weights`
corrections (it cannot wire weights); `StatTestConfig` gains **no**
field — one would move every stage-list config hash.

**Consequences.** Zero identity movement: no dataclass field, node
params serialized as written, default-path p-values byte-identical.
Rendered evidence for existing node-map runs now shows real
`n_boot`/`seed` where "—" printed, and the independence-unit line states
the generic truth ("cluster …") instead of the renderer's venue-flavored
fallback. The `test_registry` tripwire is updated deliberately. The
single-document deploy→size path unblocks.

*Review amendments (same day, adversarial pass):* (a) an UNTESTED
instrument (below MIN_CLUSTERS, sentinel p=1.0) can never be a survivor —
a weighted correction could otherwise reject `q = 1/w` and declare GO on
zero evidence; the sentinel stays in the family (it spends budget) but
cannot win. (b) The studentized signed-degenerate p is floored at half
the all-one-cluster replicate mass, `n^(1-n)/2` — the method's own
resampling floor — so an exact tie at small n can never claim more
significance than an epsilon-perturbed sample could (at n=2 the floor is
0.25; by n≈8 the add-one floor rules). (c) Degeneracy is detected
structurally (equal cluster means), not by exact `se == 0.0`, so ULP
dust cannot render a t of ~1e16 with a zero-width interval. (d) The
correction note names ADMISSIONS (a weighted correction rejecting an
instrument whose own p missed alpha) instead of reporting a negative
removal. (e) Both bootstrap functions refuse `n_boot < 1`.

---

## ADR-0034 — A declared calibration band inside the val window

**Status:** accepted (2026-08-26 — owner graduated pmquant §13 item 4)

**Context.** Calibrator fitting needs rows strictly after train (never
training data), disjoint from val (val selects models and checkpoints;
fitting the calibrator on the selection set fits it to the selection),
and strictly before everything it is applied to. Children carve this
with child-side `cuts_ms` + `block` params; `TimeSplitConfig` knows
three names plus the embargo band.

**Decision.** `cal_start_ms` (optional, appended last, omitted from
`to_obj` when `None` — the `val_start_ms` omission discipline) declares
cal as the **tail of the val window**: `val = [val_start_ms,
cal_start_ms)`, `cal = [cal_start_ms, val_end_ms]`, with
`(val_start_ms or train_end_ms) < cal_start_ms <= val_end_ms`.
`split_of` returns a fourth name `"cal"`, computed on the
policy-selected instant (ADR-0024 untouched), giving
`train < embargo < val < cal < test` by construction.
`TrailingSplitSpec` gains `cal_days` (counted back between test and
val; omitted when 0). The five three-name sites learn the fourth name;
`straddle_report` gains the cal boundaries; the planner refuses a
`split:"cal"` reader whose declared splits cannot yield a band.
Walk-forward v1: folds carry **no** cal band — a parent document
declaring one refuses pre-flight (future work). Random splits never
produce `"cal"`.

**Consequences.** Hash-frozen for every existing document (omission
discipline; assignment unchanged when unset). `$splits.cal_start_ms`
appears exactly when declared. pmquant's `block`-param workaround
retires.

*Review amendments (same day, adversarial pass):* (a) the trailing
materializer stamps `cal_start = val_end − cal_days·DAY + 1` — the cal
band is inclusive-left, so without the +1 the boundary midnight stamp
moved from val into cal (cal_days+1 daily stamps in cal, one stolen from
val); with it, cal holds exactly `cal_days` daily stamps and val never
shrinks. (b) The walkforward-vs-cal refusal also runs at PLAN time, so
`plan`/`validate` cannot bless a document whose only possible run is the
driver's refusal.

---

## ADR-0035 — Val-metric checkpoint selection in the torch pack

**Status:** accepted (2026-08-26 — owner graduated pmquant §13 item 13)

**Context.** `TorchTrain` runs a fixed epoch count and persists the
FINAL epoch's weights; `TrainingCurve` already computes
`best_epoch`/`best_value` but the tracked objective is hard-coded and
the best epoch's weights are discarded. A child wanting "keep the epoch
that minimized this metric" must own an entire train kind. Worse,
`TrainingCurve.record` silently falls back to `train_loss` when the
objective key is absent — a typo selects on the wrong signal quietly.

**Decision.** A `monitor` param on `TorchTrain._BASE_PARAMS` (covers
`DeclaredTrain` and `LinearRegressor`; excluded from the predict-side
sidecar cross-check by construction), validated at plan time against
`_MONITORS = ("train_loss", "val_loss", "logloss", "brier", "ece")` —
loss-only in v1, no direction knob; a maximize metric is refused by the
closed list, which a child may widen by declaration. At run: a
val-derived monitor with no `val_rows` refuses before epoch 1; the
curve tracks the monitor; the best epoch's `state_dict` is snapshotted
(detached, copied to CPU) and restored after the loop **before** the
final-loss recompute and `adapter.fitted`, so the persisted artifact,
the serving state, and `final_loss` all describe the selected weights;
metrics stamp `monitor`/`selected_epoch`/`monitor_value`; a monitor
that never records a finite value refuses. `TrainingCurve.record`
loses the silent fallback: an absent objective key raises naming the
objective, the epoch, and the row's keys — a tier-1 behavior change,
accepted for loudness.

**Consequences.** `state_hash` moves only for runs declaring `monitor`;
undeclared runs are bit-for-bit unchanged. The child's reason to own a
train kind shrinks to its domain residue.

*Review amendments (same day, adversarial pass):* (a) a DIVERGED epoch
under a probability-metric monitor (non-finite predictions drop the
metrics dict) records the monitored key as a present None — never the
best, never a crash — so a transient divergence restores the
pre-divergence best instead of aborting the fit; a monitor that never
sees a finite value still refuses after the loop. (b) The best-state
snapshot deep-copies non-tensor `state_dict` entries (a module's
`get_extra_state()`), which have no `.detach()`.

---

## ADR-0036 — Compressed snapshot payloads: extension-declared codecs

**Status:** accepted (2026-08-26 — owner graduated pmquant §13 item 8;
the ratified Tier-B sunset path). Amends ADR-0014's layout naming.

**Context.** Onboarding stores each acquired record twice as
uncompressed JSON (bronze `payload/` + normalized `observations/`).
Routed as-is, gz-class book archives grow ~96×, parquet-class ~10× —
the size math behind pmquant's Tier-B bypass, which the owner ratified
**with sunset at this ADR**. A model-level knob is ruled out: any new
model field moves the model hash and locks every initialized root out
at the registry pin.

**Decision.** The codec is declared by **file extension** —
`<stream>.jsonl` or `<stream>.jsonl.gz` — never a manifest field:
`relpath` is already identity material, digests stay post-compression,
`verify` stays codec-agnostic and unchanged, `_MANIFEST_KEYS` is
untouched, so manifests, `acq_id`, the model pin, and mixed-version
estates all stand. Opt-in per source via a reserved `"storage"`
namespace inside the source config's `config` object
(`{"storage": {"payload_codec", "observations_codec"}}`), closed
vocabulary `("none", "gzip")`, both defaulting `"none"`, stripped
before the connector sees config; a connector spec may not declare
`storage`/`notes` knobs. gzip is written deterministically
(`GzipFile(filename="", mtime=0, compresslevel=9)`); determinism is
per-zlib-build — asserted as write-twice equality, never pinned
digests. A new tier-1 module `codec.py` owns the mechanics. The
acquire writer closes before `build_manifest` — **load-bearing**: a
buffered writer digested unclosed would mint corrupt-at-birth
snapshots with valid evidence; a pre-commit member decode with
line-count cross-check guards it, and corrupt members surface as
`AssetError` at every seam (ADR-0020 parity). The per-row
observations append becomes kept-open writers (mandatory for gzip,
byte-parity for `"none"`). The payload codec is free to flip (nothing
external reads bronze bytes); the observations codec is a
published-contract change — flip per source only after its consumers
sniff extensions.

**Consequences.** Existing sources produce byte-identical trees
(default `"none"`); `test_snapshot.py` and `test_default_model.py`
passing unmodified is the acceptance gate. Tier-B book streams gain an
onboarding route at ~parent size, making the bypass's retirement
schedulable. Cursor/state/publication paths untouched.

*Review amendments (same day, adversarial pass):* (a) the determinism
envelope is per (CPython io/gzip layer, zlib build) — CPython's flush
behavior contributes sync-flush framing beyond the zlib bytes; tests
assert write-twice equality, never pinned digests. (b) One deliberate
codec asymmetry: gzip text pins `newline="\n"` while `"none"` keeps the
platform default (byte parity with the pre-codec tree wins there).
(c) `resolve_stream_file` resolves regular FILES only and refuses a
squatting non-file by name.

## ADR-0037 — The observations read seam: `scan_stream` / `stream_digest`

**Status:** accepted (2026-08-26 — owner ruling: generic dskit
capability first, children are wrappers only). The function-seam half
of §13 item 10; the generic reader *kind* stays open until a second
child needs it.

**Context.** The first real-data run of `children/intraday_poc` OOM'd
reading BACK the observations tree: the child's scan held 2,013,682
bars about four times over (a dedup dict, a second records list, a
third full copy at emit) and `json.dumps`'d the whole snapshot into
one string to fingerprint it — 14.3 GB peak on a single run, a
17.4 GB kill across three walk-forward folds. The same child's reader
also globbed the literal `.jsonl` spelling, silently blind to
ADR-0036's `.jsonl.gz`. Both defects are generic: every child that
reads observations re-derives the same scan, and pmquant's ladder
streams are far larger than 2M rows.

**Decision.** A tier-1 onboarding module `observations.py` owns
reading back what acquire wrote — stdlib + this package only, no
pipeline import (the sibling firewall stands).

- `scan_stream(root, source, stream, key_fields, ts_field=None,
  ts_out="asof_ms", shared_fields=())` — one deduplicated snapshot of
  `observations/<source>/*/<stream>.jsonl[.gz]`: per declared
  `key_fields` tuple the row with the LATEST `acquired_at` wins
  (bitemporal supersede, ADR-0014's comparison convention via
  `parse_utc` when `ts_field` is declared). Codec-resolved per
  acquisition dir through `resolve_stream_file`/`iter_text_lines`
  (ADR-0036: loud on ambiguity, squats, and mid-stream corruption).
  **Memory discipline is the contract:** the returned records ARE the
  winning `data` dicts (the dedup dict is drained, never copied; the
  declared epoch-ms field is added in place), and repeated strings —
  JSON object keys, `acquired_at`, and any caller-declared
  `shared_fields` values (fields that repeat heavily, e.g. a symbol) —
  collapse to one canonical copy. Deterministic order: sorted by
  `(ts_out, *key_fields)` when `ts_field` is declared, else by
  `key_fields`. A missing `observations/<source>` directory refuses
  (default-deny: a typo'd root must not read as an empty store); an
  existing source with no stream files is truthfully empty. A row
  missing a key field, a non-dict `data`, or an unparseable
  `ts_field` refuses loudly as `AssetError` (parameter problems and
  winning-level tie conflicts accumulate; store-side row refusals
  raise at the offending row).
- `stream_digest(records)` — the content fingerprint, hashed record
  by record yet **byte-identical** to
  `sha256(json.dumps(records, sort_keys=True))` (`json.dumps` joins
  list items with `", "`): the whole-snapshot string never exists,
  and any caller whose digest was frozen on the canonical dump keeps
  its identity unmoved.

**Consequences.** `intraday_poc`'s `BarsFromStore` shrinks to a
wrapper (kind name, field names, fingerprint shape); its digest does
not move, its blocked walk-forward backtest fits in memory, and a
peak-pinning test stands at BOTH layers (the generic scan and the
child wrapper). pmquant's reader lands on the same seam with a
different `key_fields`. One behavior tightens: a wrong root that
silently produced zero records now refuses by name.

*Review amendments (same day, two-skeptic adversarial pass, all
fixed):* (a) the identity freeze is an ENVELOPE, not absolute: order
is now fully key-determined, so a store holding two distinct `ts`
SPELLINGS of the same instant for one key (naive vs tz-aware, sub-ms
variants) sorts by the `ts` string where the retired code kept
scan-order ties — same content, possibly a different digest; real
acquire-minted stores with one spelling per instant are byte-frozen,
and the key-determined order is the deliberate keep. (b) an
`acquired_at` tie is adjudicated against the FINAL winner only, after
the scan: a tie AT the winning `acquired_at` dedups quietly when the
data serializes identically (the at-least-once re-pull) and refuses
when it differs — no bitemporal winner exists — while a tie a later
acquisition supersedes is history, never a refusal. This makes both
the dedup content and the accept/refuse outcome scan-order-independent
(the retired sorted-glob order could flip winners across
prefix-related dir names). (c) enumeration is `os.listdir`, never a
glob — glob metacharacters in a caller's `root`/`source` silently
scanned a full store as empty; a stream file sitting directly under
`observations/<source>/` (outside any acquisition dir) refuses as
tamper-shaped. (d) `RecursionError` joins the loud family around both
`json.loads` and `json.dumps` — a pathological nested line crossed
the seam raw. (e) a 0-byte `.gz` member refuses in
`codec.iter_text_lines` (corrupt-shaped — a valid empty member always
carries header + trailer; `gzip.open` hands back silent EOF), while a
valid empty member still reads as zero lines. (f) two more
tightenings join the wrong-root refusal: a record already carrying
`ts_out` refuses (the retired code silently overwrote it in its
copy), and `.jsonl.gz` members are now READ (the retired glob
silently scanned a gz-only store as empty). (g) both peak pins
tightened (peak < 800, resident < 700 B/row) so a whole-dump digest
regression alone (~930 B/row measured) fails them, not just the full
defect (~1550).

*Second-round amendments (same day — fresh skeptics re-reviewed the
first round's fixes and both independently broke its tie rule; fixed
red-first):* (i) the first-round refusal fired against the RUNNING
maximum, so a same-second conflict that a later acquisition had
already superseded refused anyway — permanently, since observations/
is append-only and every corrective pull sorts after the tie — and the
outcome flipped with directory arrangement. Ties are now recorded
during the scan and judged only against the final winner (the rule as
stated in (b) above). (ii) tie identity was Python `==`, which coerces
`100 == 100.0 == True` — a type-respelled same-second re-pull dedup'd
quietly with a hash8-order-picked winner, moving the emitted value's
TYPE and the digest; identity is now the canonical
`json.dumps(sort_keys=True)` serialization. (iii) documented, not
changed: `int()` sub-millisecond truncation in the epoch-ms flatten is
one ms off true floor for pre-1970 and ~2112+ sub-ms stamps —
inherited from the retired code, digest-frozen, called out in the
docstring.

*Third-round amendments (same day — the loop's escalation trigger
fired: three consecutive fresh-skeptic rounds each surfaced
correctness defects, so the loop stops here and the merge decision
goes to the owner; all round-3 findings are fixed red-first):* (i) a
NaN key value refuses at intake — NaN neither equals nor orders
against anything WITHOUT raising, so the sort's `TypeError` guard
never fired and record order (and digest) went silently
scan-order-dependent. (ii) tie adjudication filters to the winning
level BEFORE sorting and sorts the problem STRINGS — the second-round
code sorted raw key tuples first, so type-heterogeneous keys crashed
raw `TypeError` even on a store whose winners were unambiguous.
(iii) `acquired_at` adjudicates on the parsed INSTANT (`parse_utc`,
one parse per distinct spelling), never the string: lexicographic
comparison ranked a later `-05:00` stamp below an earlier UTC one and
let two spellings of one instant dodge the tie rule; an unparseable
stamp refuses, a missing one reads as the earliest possible instant.
Acquire-minted stores (fixed-width single-spelling `utc_now`) are
unaffected — the freeze envelope holds. (iv) the drain-time
`ts_field` refusal names the key. (v) the README now routes
observation readers through the seam (it still pointed consumers at
hand-rolled `resolve_stream_file` sniffing) and documents how to
leverage it.

*Fourth-round amendments (2026-08-26, the owner's continue-until-clean
ruling; two fresh skeptics per round; the round-3 adjudication itself
held under 600×6 permutation fuzzing and a 200-store freeze check):*
(i) dedup-KEY identity is canonical, never coercing Python `==` —
dict hashing let `1`/`1.0`/`true` share one slot, so a later
acquisition silently superseded records it never keyed; key values
are now type-tagged for keying AND sorting (strings pass through
untouched — zero allocation, the memory contract is unchanged;
floats tag their repr so `-0.0`/`0.0` stay distinct), closing the
same coercion family the second round closed for data identity.
In-envelope digest freeze unaffected (homogeneous keys order as
before). (ii) `parse_utc` catches `OverflowError` — a boundary-year
offset stamp (`9999-12-31T23:00:00-05:00`) parses as ISO and then
leaves the year range in `astimezone`, which escaped raw through
both stamp paths; every caller inherits the typed refusal. (iii) a
DIRECTORY squatting the stream spelling at source level refuses like
the file spelling (it scanned as an empty acquisition dir). (iv)
documented: `acquired_at` adjudication is millisecond-resolution
(sub-ms-apart stamps collapse to one level, loud direction only) and
refusal message WORDING may vary with directory arrangement while
outcomes never do.

*Fifth-round amendments (2026-08-26, continue-until-clean; the
round-4 identity fix held a 4,225-pair canonicity fuzz and a 300-store
sort fuzz):* (i) float keys SORT NUMERICALLY — the round-4 tag put
repr first, freezing repr-lexicographic order (`[-1.0, -2.0, 10.0,
2.5]`) into the digest-to-be; the tag is now `(value, repr)` so order
is numeric with the repr as the `-0.0`/`0.0` tiebreak (NaN never
reaches the sort — intake-refused), and the round-4 claim "homogeneous
keys order as before" is corrected: it held for str/int, not float.
Caught before any consumer froze on the wrong order. (ii) `source`
and `stream` must be SEGMENT-SAFE (`_check_segment`, the writer's own
rule): the reader accepted any string, so `"../../../secrets"` read a
file OUTSIDE the store, `"alpaca/../polygon"` read a sibling source
under the wrong name, and writer-impossible typos (`"Bars "`) scanned
silently empty. The seam refuses them at parameter check (the child
wrapper inherits the refusal at resolve). (iii) a 0-byte stream
member of EITHER spelling refuses at the seam — the committed writer
lazy-opens on the first record, so a committed member always holds a
line; the codec-level refusal stays gz-only (other `iter_text_lines`
callers may read legitimately empty text). (iv) tie-refusal messages
show the raw key values (a float key `1.0` no longer prints
indistinguishably from a string key `'1.0'`).

*Sixth-round amendments (2026-08-26, continue-until-clean; the
round-5 fixes took a clean PASS from their dedicated lens — 1,891-pair
identity fuzz, 3,050-candidate reader/writer acceptance equivalence
with zero mismatches, the traversal family dead, a 60-store freeze
check clean):* (i) epoch milliseconds are computed in exact INTEGER
arithmetic — `int(timestamp() * 1000)` compounded two float roundings,
landing exact-ms stamps one ms wrong from ~2038 on (and pre-1970) and
collapsing `acquired_at` stamps a FULL millisecond apart into one
instant, which could spuriously and permanently refuse a valid
supersede. This corrects the round-2 (iii) and round-4 (iv)
statements: the "inherited edge" was underdescribed. Sub-ms
remainders now FLOOR in every era; digests move only for stores that
actually hit the defect (none in-repo — second-precision writer
stamps and 2026 minute bars are float-exact). (ii) documented, not
changed: cross-family key order is tag-lexicographic (`bool < float <
int`), deterministic and digest-stable; decodable-but-empty members
(a valid empty gz member, a whitespace-only plain member) read as
zero rows — writer-impossible shapes that cannot hold lost data,
while the 0-byte refusal targets partial copies; and a DANGLING
symlink squatting the stream spelling is silently skipped — a
pre-existing ADR-0036 `resolve_stream_file` behavior on `main`
(`os.path.exists` is false for it), unchanged by this branch and
declared here rather than fixed.

*Seventh-round correction (2026-08-26; the `_epoch_ms` code itself
took a clean PASS — a 1.2M-sample exactness proof against a Fraction
oracle, bit-identical freeze through the whole seam, 1.10× drain
cost):* the sixth round's era claim was itself underdescribed. The
retired `int(timestamp() * 1000)` recipe was one ms wrong for
~1-2.5% of MILLISECOND-precision stamps in affected decades WITHIN
1970..2037 as well (first counterexamples already in 1970) — only
exact-SECOND stamps, the writer's `utc_now` envelope, were
era-independently exact, so "none in-repo" stands and digests still
move only for stores that actually hit the defect. Also from this
round: the two-key tie-accumulation test's assertion was
half-tautological (`"1" in text` matched any path:line material) and
now matches the key spellings and counts the problems.

*Eighth-round amendments (2026-08-26; the free-sweep lens returned a
full PASS — a 600-store differential fuzz against an ADR-derived
reference with zero mismatches, the verbatim backtest config executed
end to end over a gzip superseding acquisition, packaging/import/
hash-seed all clean — and the seventh round's era corrections were
confirmed by measurement):* (i) a present-but-EMPTY `acquired_at`
string refuses like every other unparseable spelling; only true
ABSENCE of the field reads as the earliest possible instant. The
empty string was silently conflated with absence — writer-impossible,
corrupt-shaped, bounded (it could never flip a winner or move a
well-formed store's digest), but the wrong direction. (ii) declared,
not changed: duplicate names in `key_fields` are accepted harmlessly,
and `ts_field == ts_out` refuses via the record-already-carries
message rather than a parameter-check message — both loud-or-harmless
directions.

*Ninth-round amendments (2026-08-26; the free-sweep lens returned a
full PASS — the first cal-band document ran end to end with a gzip
supersede composing ADR-0034/0036/0037 in one run, 500k-row scaling
measured linear, a 600-store differential fuzz clean):* (i)
permission denial refuses instead of reading as absence — the boolean
stat probes (`os.path.isdir` in the scan gate, `os.path.isfile`/
`exists` in `resolve_stream_file`) returned False on EACCES, so a
mode-000 acquisition dir silently vanished from the bitemporal dedup
(a SUPERSEDED row served as winner, digest moved without a word) and
an untraversable source dir scanned a correct root as empty. Both
sites now `os.stat` and refuse on any `OSError` except
ENOENT/ENOTDIR, matching the assets engine's chmod-denial pins; the
declared dangling-symlink skip is unchanged (`FileNotFoundError` maps
to absence). `validate`'s snapshot reader inherits the loud refusal
through `resolve_stream_file`. (ii) `stream_digest`'s refusal message
says "cannot be canonically serialized" — the old "not
JSON-serializable" misdescribed the unorderable-mixed-key-types
`TypeError` from `sort_keys`.

*Tenth round — the clean pass (2026-08-26).* Both fresh lenses
returned zero blocker/major/correctness findings: the verification
lens (1,200-store three-way differential fuzz — branch vs pre-round-9
seam vs a clean-room reference — zero mismatches; ELOOP/FIFO/socket
squats typed; the validate CLI inherits the denial refusals; 120/120
freeze stores byte-identical; both memory pins green) and the free
sweep (mutation-testing the suite, 400-store fuzz, 300k-datetime
`Fraction` oracle, public-surface kwarg misuse, wrong-root
interactions — production code unbroken). Post-pass, with NO
production change: two mutation-proven test gaps were closed as green
pins (the sub-ms FLOOR and `-0.0`/`0.0` key distinctness — both
documented, digest-relevant invariants the suite did not yet pin),
and one nit is declared-deferred: `os.path.isdir(base)` on a
permission-denied `observations/` PARENT refuses via the wrong-root
message rather than a denial diagnosis — every path through that
branch refuses, so denial can never read as an empty store; only the
wording misattributes.

## ADR-0038 — `TrainableNode`: the mode dispatch becomes a template method

**Status:** accepted (2026-08-28; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)

**Context.** Nine `run()` methods across five modules hand-roll one `mode`
dispatch: fit kinds branch to a load path (`sklearn.py:571`,
`synthetic_nodes.py:257`, `torch.py:960`, `transformers.py:457`, `sb3.py:323`);
pinned-inference kinds refuse `mode="train"` by name then resolve an artifact
reference (`sklearn.py:739`, `torch.py:1237`, `transformers.py:699`,
`sb3.py:426`); two `validate_inputs` restate the guard. CLAUDE.md names the
smell. The conformance bar can only SNIFF — it asserts `"mode"` appears in a
trainable's bytecode (`conformance.py:1213-1227`) — because no class expresses
the contract structurally.

**Hard constraints (TODO 3c), restated.** Do NOT split each trainable into train
and load classes: `mode` is a node-level document field INSIDE the identity hash
(measured `4039ddf1…` → `2c9d9925…`). Existing class names must NOT change —
`transformers.py:612` refuses any artifact whose sidecar `node_class` mismatches,
and `torch.py:708/776-790` additionally pins the recorded class ref AND the
identity of the `build_module` function reached through the MRO, so relocating
that mixin method would orphan every existing `.pt` artifact. Torch and sklearn
would stay green, so the suite would not catch either. Port order: sklearn →
synthetic_nodes → torch → transformers → sb3.

**Decision.** `TrainableNode(Node)` in `dskit/pipeline/node.py` (tier-1,
stdlib-only), never registered — `node_class_errors` refuses abstract classes.

- **Hooks.** Two `@abstractmethod`s — `run_train(ctx, inputs)` /
  `run_load(ctx, inputs)` — plus `default_mode` (what an UNSET `mode` means) and
  a read-only `effective_mode = self.mode or type(self).default_mode`.
  **Named `run_<mode>`, NOT `train`/`load`:** the legacy model-family protocol
  owns that pair (`registry.py:96-106`) and `resolve.py:273-282` refuses a
  `model.name` class lacking callable `train`/`load`. Reusing the names would
  disarm a live plan-time guard and degrade its refusal to a mid-run
  `AttributeError`.
- **`run()` becomes the template method:** `effective_mode == "load"` →
  `run_load`, else `run_train`. Pinned-inference kinds set
  `default_mode = "load"` and implement `run_train` as today's refusal — exception
  type and wording verbatim, since the behavioural bar reads the message.
- **`validate_inputs` becomes a second template method**, dispatching additively
  to `validate_common_inputs` then, BY `effective_mode`, `validate_train_inputs` /
  `validate_load_inputs`. Assignment is decided per class in the port; a shared
  message is written once, pack-locally.
- **Two services move to `Node`, not `TrainableNode`**, because a non-trainable
  caller (`Sb3Eval`, role `score`) already carries copies: `pin_port_problems`
  (the non-empty check written three times) and `pinned_artifact`, which resolves
  in order — a node-level pin contradicting a declared param refuses ("one pin,
  not two"); else the node-level pin; else the declared param, then the wired
  port; refusing by name when none is present. **A falsy value counts as absent
  at every step**, matching every call site being replaced (`x or y` throughout)
  — so `params: {"artifact": null}` keeps refusing by name rather than crashing
  downstream.
- **`node_level_pin()` is a hook, never `isinstance(self, TrainableNode)` inside
  `Node`** — a type test there is the branch this ADR deletes, relocated to the
  most-inherited class in the package. It is the design's only raw-`mode` read,
  because a node-level `artifact` exists **iff** the document wrote `mode="load"`.
  `planner.py`'s plan-time document check is untouched and unweakened.
- **Port order and the base-order rule.** Re-parent from `Node`: `SklearnFit`,
  `SklearnPredict`, `SynthTrain`, `TransformerFit`, `TransformerPredict`, and
  `_TorchModel` ONCE (covering the torch pairs and their declared subclasses). In
  sb3 only `Sb3Train` and `Sb3Policy` — NOT `_Sb3Base`, which `Sb3Eval` also
  inherits. **The bar is that BOTH template methods resolve to `TrainableNode`:**
  a pack base may precede it only if it defines NEITHER `run` nor
  `validate_inputs`. Kind names, `_PARAMS`, roles and output contracts are
  untouched.
- **The conformance bar becomes structural.** The bytecode sniff becomes: for
  every kind of a trainable role, assert `issubclass(cls, TrainableNode)` and that
  `run` and `validate_inputs` are still the base's. The sniff cannot merely stay —
  after the port a child's own code never names `mode`, so it would hard-fail
  every ported class. The validation half is the likelier breach, so it names the
  hook to override instead.
- **The MRO walks must SKIP the new base, through one seam.** Three conformance
  loops walk `cls.__mro__` breaking at `Node`; inserting a class widens all three,
  and `TrainableNode.node_level_pin` reads `self.artifact` — a DECLARED knob of
  three pinned-inference kinds, which the base would then vouch for unread.
  One private `_evidence_bases(cls)` generator encodes the rule (walk, break at
  `Node`, `continue` past `TrainableNode`) and all three loops iterate it.
  Toolkit code is never evidence about a child.
- **`save` is deliberately NOT a hook** — four persistence models share no shape
  a tier-1 signature could pin without restating tier-2 truth, and that seam is
  already pinned behaviourally.
- **The selector seam (ADR-0042) is NOT decided here.** TODO directs it be
  `TrainableNode`'s SIBLING; this ADR guarantees either shape works, because the
  services a fitted non-model node needs sit on `Node`, reachable without
  inheriting the dispatch.

**Consequences.** **Zero identity movement, provably:** the hash is a function of
the document JSON alone, and no document field, param tuple, kind name, default or
class name changes. Behaviour deltas, all narrow: pinned-inference kinds under
`mode="load"` now refuse a contradicting `params.artifact` instead of silently
preferring one; `TransformerFit` under load stops demanding a `rows` wire it never
reads; two predict kinds with an empty node-level pin now refuse where they fell
through to a param — the price of keeping torch's and sb3's stricter rule as the
single one. The last are document-unreachable and need direct construction.
Refusal wording keeps every substring the existing tests match. Nine `run()`
branches, two guards and three duplicated port checks delete; an incomplete
trainable now refuses at CONSTRUCTION rather than at call time. The cost is real:
the bar constrains SHAPE — a pack wanting to wrap `run()` must override a hook —
and `TrainableNode` becomes a published extension point owed compatibility.

## ADR-0039 — A `foreach` section: declared fan-out over a key list

**Status:** accepted (2026-08-28; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)

**Context.** "One model per symbol" is written longhand: a third symbol in
`run-train.json` means duplicating the filter + trainer pair — `qhat_msft` is
`qhat_aapl` with its input reference changed and byte-identical params — and the
duplicate puts N unpinned space keys in any search node, so two symbols can
silently tune to different architectures. The reference grammar has only
`$node.path` and `$prev`, and an inventory finds no subgraph expansion of any kind
(`join`'s `allow_fanout` is row-multiplication, not this). But the precedent
exists: `run_walk_forward` already derives N documents from one via `to_obj` →
mutate → `from_obj`. Owner's scoping, restated: fan-out over a **declared key list
only**; **no expressions, no conditionals**; a `foreach` **is identity**; existing
hashes **provably unmoved**.

**Decision.** A top-level `foreach` section, expanded at document construction:
the document **stores what was written and derives what runs**.

**Grammar.** A `ForeachSpec`, one per document, no nesting:

```jsonc
"foreach": {
  "keys": ["aapl", "msft"],                  // non-empty; unique non-empty strings; none starting "$"
  "pipeline": { "rows": {…}, "qhat": {…} },  // the template subgraph, non-empty
  "notes": ""
}
```

Each template is normalized exactly as a node is, so `{"uses": "x"}` and its
fully-spelled twin hash identically. `keys` is SORTED and pinned as a tuple —
keys are a set, so sorting beats refusing-unless-sorted. A key beginning `$`
refuses, because rule 3 substitutes the raw key as a params value and `"$window.records"`
would expand into a live reference. `pipeline` stays required but may be `{}` when
a `foreach` is declared; the CLI's node-map sentinel widens to match, or a
`foreach`-without-`pipeline` file would fall through to the legacy stage grammar
and print the wrong error.

**Data model.** `PipelineDocument` gains ONE identity field, `foreach` (hash
material, type-guarded like every peer section), and two DERIVED fields:
`expanded` (shared nodes with fanned-out ports, plus every instance) and
`foreach_groups` (template key → instance keys). The derived pair is **never
emitted by `to_obj`**, so it is provably not hash material — the hash reads
`to_obj` only — and `expanded` **is `self.pipeline` itself when `foreach` is
absent**, so every engine site that switches to reading `expanded` is
byte-identical today. Seven sites make that switch: plan, `Plan.to_obj`, the
search seam, the resolve and execute loops, the cross-field checks, and
`validate`'s node count, which reports what RUNS.

**Expansion**, per key in sorted order, under the same guard as the other
cross-field checks. Emission order is fixed — declaration order for shared nodes,
then template-major/key-minor — because the toposort breaks ties on it.

1. **Suffixing.** `t` → `t__<slug>`. Shared, template and instance keys must be
   pairwise distinct; a collision refuses naming both provenances.
2. **Reference rewrite.** Inside a template, `$t.path` and `$prev` targets naming
   a template key rewrite to the instance. Shared-node and `$splits` references
   pass untouched.
3. **The `$each` token.** In template `params`, a value that is EXACTLY `"$each"`
   becomes the key string, recursively at any depth. Whole-value only, never
   substring interpolation — that is the line between fan-out and templating. As a
   params dict KEY it refuses: key substitution is not built, and letting the token
   ride onto all N instances is the literal ride-through the grammar exists to
   refuse. Outside a template it stays legal and untouched.
4. **Port fan-out is OPT-IN.** A shared node fans a port out only when the port is
   written `<base>__each` and its value names a template key. Automatic fan-out was
   rejected because `Node` declares no port set, so the engine cannot know which
   ports are fannable.

**Search spaces come for free.** A space key naming a template param expands per
instance, which is the point: the N duplicate keys that nothing pinned become one
declaration.

**Not decided here.** No new plan-time refusal keyed on split kind: the one
considered — refusing a `data`-role template under trailing splits — would reject
documents whose walk-forward path never materializes that split at all, since
folds replace the splits section wholesale. The driver's existing run-time rules
govern; this ADR adds no refusal the document cannot already earn.

**Consequences.** Existing hashes provably unmoved: `foreach` is optional and
emitted only when present, the derived fields never reach `to_obj`, and with no
`foreach` the expanded map IS the declared map — the same object. A `foreach`
document's identity covers the keys and the template, so adding a key is a
different computation and a different identity, which is correct. The engine gains
one section, one spec type and one expansion pass; nothing gains a mode branch.
The child's longhand duplication collapses, and its search space stops being N
unpinned copies. The cost: a second key namespace (instance keys are generated, so
a document's node names are no longer all literal), and `foreach` is one more
thing a reader of a document must know. **Deferred:** nesting, expressions, and
key substitution in params keys — each would move this from fan-out toward
templating, which the owner ruled out.

## ADR-0040 — Gap-aware vectorized windows in the numpy pack, and the fitted-transform family

**Status:** accepted (2026-08-28; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)

**Context.** The tier-2 array seam is the mandated home (extend `ArrayFeatures`,
do NOT build a new seam), and three defects block real use. (1) `_lift` is welded
to the `MarketRecord` envelope — it groups on `instrument`, orders on `asof_ms`
and lifts exactly the envelope four (`libs/numpy.py:106,174-188`), so a keyed
time series with other names cannot enter. (2) The pack RESTATES tier-1 truth:
`_price_ok`/`_lead_ok` (`:113-120`) re-derive `records.py:69-76,150-155`, and
they fail SILENTLY through the writeback pass-through — audit HIGH-2. (3)
Positional offsets bridge session gaps: `TrailingReturns` computes
`mid[window:] / mid[:-window]` straight across any boundary (`:682`).

The child mirrors all three: `WindowRows` welds `symbol`/`asof_ms` while
`price_field` IS a knob, writes every default twice, keeps a private
`_reject_unknown` that tier-1 made public, and restates the chain semantics a
third time in `live.py:latest_feature_row` on a hardcoded price field — the
train/serve skew of audit HIGH-4. Gap discipline is the one thing the child gets
right, and a naive port would lose it.

Separately, nothing normalizes features (folded in per the TODO). A scaler fits
on a DECLARED split and carries to the others, so it is stateful — and the
causality guard DEPENDS on `apply` being pure, re-running it on prefixes and
refusing drift. A fitted transform is structurally forbidden in `_ArrayApply`.
That story is designed ONCE below; the selector seam (ADR-0042) is its second
consumer and conforms to it. This record carries both halves because that is the
owner's sequencing, not two independent decisions.

**Hard constraints, restated.** All 14 identity hashes stay byte-identical: this
is a refactor plus defaults-off knobs. That also forbids ADDING a param to either
child document. The pack must IMPORT tier-1 record rules, never restate them.
Vectorized — the 2M-bar run is the benchmark. End state: `WindowRows` collapses to
one `apply()` under the lookahead screen, and `live.py:latest_feature_row` is gone.

**Decision.**

**1. Declared lifting fields, read through hooks.** `_ArrayApply._PARAMS` gains
`group_field`, `order_field`, `fields`, `max_gap`; `ArrayFeatures` adds
`carry_fields`, `require_fields`, `drop_incomplete`. Each is read through a
public one-line accessor returning `self.params.get("<literal>", <CONSTANT>)`, so
a subclass whose document speaks different SPELLINGS overrides the accessor
instead of forcing its knobs into the pack's names. Defaults are module constants
named once and reproduce today's behaviour exactly.

**An accessor override NARROWS `_PARAMS`, and hardcoding IS an override.** A
subclass overriding an accessor MUST drop that knob from `_PARAMS`, or
default-deny accepts a value the run discards. This also closes a live hole in the
shipped subclasses: `LogMid` and `TrailingReturns` index `arrays["mid"]`, so a
document writing `"fields": ["bid"]` would validate clean and die at execute with
a bare `KeyError`. Both override `fields()` and give up the knob. Because
`validate_params` is a classmethod it cannot evaluate an accessor, so **every
per-knob check is guarded by `if "<knob>" in cls._PARAMS`** — without that guard
the narrowing rule is unplannable and `validate` would refuse both child
documents. One test pins the rule in three directions.

`_lift` reads the accessors and exposes the order array under its DECLARED name.
The order predicate is part of that contract: a non-bool `int` (today's rule) or a
finite `float`, with `max_gap` in those units. Since per-record failures pass
through and never raise, a declared field matching NOTHING would emit zero rows and
exit 0 — so `_ArrayApply` REFUSES by name when the input is non-empty and every
record was unlifted. `ArrayMap`'s writeback table does NOT widen: `fields` governs
what `_lift` READS, never what `ArrayMap` writes, because a foreign name has no
acceptance predicate the pack could honestly supply.

**2. Import tier-1 truth (HIGH-2).** `records.py` exports its price and lead
predicates; the pack imports them and its private copies die. A tier-2 pack never
re-derives a core validator.

**3. Gap-aware framing.** `max_gap` splits each ordered group into segments before
any offset arithmetic, so no lag, lead or return ever spans a session boundary.
Absent `max_gap` reproduces today's behaviour exactly. This is the one thing the
child got right, now owned by the pack and inherited with the causality screen.

**4. The ops.** Group, order, gap-split, log/pct return, lag N, and lead N (the
forward label) — vectorized, with the lookahead screen applying to every one.

**5. The child collapses.** `WindowRows` becomes one `apply()` over the pack,
keeping its own knob spellings via accessor overrides, and inherits the causality
screen it never had. `live.py:latest_feature_row` is DELETED in favour of a
`latest_rows` call on the same node, which is what kills the train/serve skew: the
serving path stops restating the chain and reads the declared price field. The
existing parity test is REPLACED, not deleted, by one that compares a serving row
against the training row for the same `(group, order)` — a mechanism-only parity
test cannot catch a differing FIELD, which is the actual defect.

**6. The fitted-transform family — the authoritative story.** A fitted transform
learns state from a DECLARED split and applies it elsewhere, which the purity
assumption forbids, so it is an explicit SIBLING of the pure-transform family,
never a slot in it. One new tier-1 module, `dskit/pipeline/fitted.py`,
stdlib-only — the `codec.py` / `observations.py` precedent.

- `FittedTransform(TrainableNode)` — abstract, new role `"fitted_transform"`,
  outputs `("transform", "rows", "metrics")`, params `fit_split`, `order_field`,
  `purity_check`. It subclasses ADR-0038's `TrainableNode` and overrides NEITHER
  template method: mode handling is A's dispatch, the mode hooks are A's
  `run_train`/`run_load`, and input validation rides A's additive seam. No
  subclass and no consumer ever sees `mode`. If ADR-0038 does not land, the family
  carries the identical hook pair over a private one-line dispatch and re-parents
  mechanically — role, params, hooks, sidecar and every hash unmoved.
- **Two `@abstractmethod` hooks:** `fit(rows, params) -> state` (a JSON-able
  dict) and `apply_state(state, rows, params) -> rows`, which must be pure and
  ROW-INDEPENDENT. Purity lives per hook, not per node. `transform` carries a
  carrier binding (class, state) with `.apply(rows)`.
- **The `rows` port carries EVERY input row, transformed, in both modes.**
  `fit_split` governs what the state is LEARNED FROM, never what is emitted —
  the deliberate departure from the score node's skip-outside-split precedent,
  because a scaler emitting only its fit slice would silently truncate the stream
  its downstream reads. Applying a train-fit state to val/test rows is the
  REQUIRED behaviour; the leak would be FITTING on them.
- **Leakage is refused at plan where the document can be read, and at run
  otherwise.** `fit_split` is required under train mode and must name a declared
  split. **When the document declares no splits at all, a declared `fit_split`
  refuses at plan by name** rather than silently fitting on everything. **Under
  load mode nothing is fit, so `fit_split` is not required**; when present it is
  checked against the sidecar's record of what the state ACTUALLY saw, and a
  disagreement refuses rather than letting the document misdescribe a restored
  state.
- **A mechanical screen, not a proof.** `purity_check` (default true, the
  `causality_check` idiom) re-applies `apply_state` to a sampled row ALONE and
  refuses when the answer differs from that row's answer in the full call —
  catching the family's classic leak, an `apply_state` that recomputes a statistic
  over the rows it was handed. Turning it off is a decision the document owns.
- `ApplyTransform(Node)` — concrete kind `"apply-transform"`, role `transform`,
  inputs `("transform", "rows")`: projects a SECOND stream through a wired
  carrier. ONE apply kind serves the scaler, ADR-0042's selector, and every later
  fitted transform.
- The first member is a standardizing scaler, fit on train only.

**Consequences.** All 14 existing identity hashes unmoved — every new knob is
absent-by-default and the child documents gain no param. The numpy pack stops
restating tier-1 record rules, so loosening a bound in `records.py` can no longer
silently drop legitimate writebacks. The child sheds its private helper, its
doubled defaults and its third copy of the chain semantics, and gains a causality
screen it never had. The engine gains one tier-1 module, one role and two kinds;
`TRAINABLE_ROLES` widens to include `fitted_transform`, so ADR-0038's structural bar
covers the new family for free. The cost: a published extension point with a purity
obligation the base can screen but not prove, and one more role in the planner's
vocabulary. **Deferred:** widening `ArrayMap`'s writeback table to foreign column
names is a separate decision, not taken here.

## ADR-0041 — A time-series architecture zoo: one node pair over an arch registry

**Status:** accepted (2026-08-28; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)

**Context.** Children rewrite standard nets: `intraday_poc` hand-rolls a plain
LSTM regressor over a flat lag vector. Nothing about it is domain knowledge, and
the next child would write it again. The owner's ask is that a project start by
NAMING an architecture instead of writing one. One constraint decides the shape:
the purity gate forbids an `nn.Module` subclass at module level anywhere in
`dskit/pipeline/` — it scans class bodies too — so the sanctioned pattern is the
existing `_LinearModule`, which defines the net INSIDE `build_module`. The sidecar
compares `build_module` FUNCTION identity, so artifact loading stays safe.

**Decision.**

1. **One new tier-2 module**, `dskit/pipeline/libs/torch_ts.py` — the pack's
   CATALOG beside its engine. `torch.py` owns the artifact/loop protocol and stays
   BYTE-IDENTICAL, so the zoo carries zero risk to its four registered kinds.
   Purity is placement-independent; the sibling is scanned identically.
   Registration is pack doctrine: its own `NODE_KINDS` and an explicit
   `register()`. The "one module per library" line is amended to name it.

2. **One node pair** — `TimeSeriesTrain` / `TimeSeriesPredict`, kinds
   `torch-ts-train` / `torch-ts-predict` — over ONE `_TsModel` mixin, so
   `build_module` is a single function and the sidecar class-match passes across
   the pair. **`arch`, `head` and `seq_len` are REQUIRED on the trainer and
   OPTIONAL on the predictor**, through the pack's existing required-knob hook
   rather than a new mechanism. Optional-on-predict is load-bearing, not cosmetic:
   a predict node pinning `arch` would kill the sweep below, because a rerun
   rebuilds descendants from their own params and every off-arch trial would die
   on the cross-check. The predictor builds nothing from its own `arch` anyway —
   the module comes from the sidecar. Where the trainer left a knob defaulted, the
   pair compares against the DEFAULT rather than by presence, so a defaulted knob
   stays pinnable.

3. **One input contract — no "sequence arch" class.** Every arch maps
   `(B, seq_len, channels)` to `(B, 1)`. `seq_len`, `channels` and `order` are NODE
   knobs, because they describe the dataset, not an architecture; `channels` is
   DECLARED, never derived. Types and bounds are checked with the tier-1 helper the
   pack already imports, and **they run BEFORE any arithmetic** — the totality fuzz
   substitutes `None` and `{}` into every declared knob, so a `seq_len * channels`
   reached first would explode inside `validate_params` and fail the "a validator
   must RETURN problems, never explode" bar. Every cross-knob check, including
   `len(features) == seq_len * channels`, runs ONLY when both names are declared and
   cleared their own checks; a predictor omitting `features` skips it, safe because
   the module is rebuilt from the sidecar. Equality is the point: a derived
   `len(features) // seq_len` would accept every divisor, silently training a
   two-channel model over one channel of lags. The flat row is pinned
   CHANNEL-MAJOR, with `order` naming the lag direction, and the ONE reshape lives
   in `build_module`, never in a builder.

4. **The registry.** `_ARCHS: name -> {build, problems, defaults, doc}` — the
   split-policy metadata shape — plus `register_arch(name, build, *, problems,
   defaults, doc="")`, with `problems` and `defaults` required keyword-only so an
   arch cannot enter unvalidated. A registry table is the repo's sanctioned middle
   ground; a string switch inside `run()` is what the pillars forbid.
   **`arch_params` is keyed by ARCH NAME** — `{"lstm": {…}, "dlinear": {…}}` — so
   ONE document carries knobs for every candidate and `space: {"model.arch": [...]}`
   sweeps architectures directly. **Arch names use the node-key character class
   (underscores, not hyphens)**, because they are also `arch_params` keys and a
   hyphen would put a second spelling rule in the grammar. Plan-time validation
   runs the SELECTED arch's defaults-merged params through its own `problems`
   function, and additionally every declared `arch_params` sub-dict, so a candidate
   is checked before a sweep ever reaches it; a sub-dict that is not a mapping is
   refused by name rather than handed to a builder's validator.

5. **Ships:** `dlinear` and `nlinear` FIRST — the honest baselines (Zeng et al.
   2023); if an LSTM cannot beat DLinear on your series, that is the finding — then
   `mlp`, `lstm`, `gru`, `lstm_attn`, `gru_attn`, `tcn`, `cnn1d`, `patchtst`.
   N-BEATS excluded as heaviest and least general.

6. **The head, and how it consumes the `loss` knob.** `_HEADS: name -> adapter
   class`, `"regression"` and `"binary"`; an unknown head is refused at plan naming
   the vocabulary. **There is no `register_head`, deliberately** — a head is an
   interpretation of the shared `(B, 1)` output plus a default objective, and the
   pack already has the open doorway for objectives: the `loss` import path. So a
   head SELECTS a default loss and a document overrides it by naming one, which is
   why the zoo needs no head-dependent output width and no `if head ==` chain.
   Binary markets are first class, matching the existing accounting split.

7. **The child follows.** `intraday_poc` NAMES the zoo LSTM and its hand-rolled
   `NextBarLSTM` is deleted — that switch is the proof the zoo is generic.
   `models.py` STAYS, permanently, as the seam for an architecture a project
   genuinely invents. The child's own `lookback >= 2` refusal is replaced by the
   pack's `seq_len` floor. Its document hash MOVES, intentionally and declared.

**Consequences.** Zero identity movement for every existing document: `torch.py`
is byte-identical, no existing kind, param tuple or default changes, and the new
kinds are new names. The child's two documents move by design when they adopt the
zoo, which is a declared ledger entry, not drift. A child stops rewriting standard
nets; a new architecture is a `register_arch` call, and architecture becomes a
SWEPT param rather than a document edit. The purity gate is untouched and still
passing, because every net is defined inside `build_module`. Costs: a second torch
module to keep current, a published registry the toolkit owes compatibility to, and
ten builders whose numerics the toolkit now maintains. **Deferred:** `register_head`
(closed on purpose above), N-BEATS, and any arch needing an output width other than
`(B, 1)` — that would reopen decision 3's single contract.

## ADR-0042 — Feature selection: a fitted transform whose state is the surviving columns

**Status:** accepted (2026-08-28; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)

**Context.** Feature selection is genuinely absent: a grep finds zero selectors
anywhere in `dskit/`. It is also the one capability the owner's model-selection
design needs that cannot be assembled from existing parts, because **a selector is
FITTED**. It learns which columns survive from TRAINING data, while every existing
transform — `Filter`, `Derive`, `Concat`, `Join`, `ArrayMap`, `ArrayFeatures` — is
stateless and pure, and `_ArrayApply`'s causality guard DEPENDS on that purity: it
re-runs `apply` on truncated prefixes and refuses when the output moves, which a
fitted selector trips by design. So it cannot be a slot in the transform family.

**Leakage is the one hard rule.** A selector that sees validation rows leaks
invisibly — nothing fails, the scores just come out better. The seam must make
"which split did you fit on" DECLARED and checkable at plan, the way the `score`
role already declares `split`.

**Relationship to the sibling drafts, decided WITH them, not against them.**
ADR-0040 designs the fitted-transform family — `FittedTransform`, its `fit` /
`apply_state` hooks, the `fit_split` rules, the purity screen and the
`apply-transform` kind — and names this seam its second consumer. This ADR does
not build a second seam; it adds ONE member to that family. TODO.md asks that the
selector be `TrainableNode`'s **sibling**, and the reason it gives is "or the two
abstractions get designed against each other." That reason is honoured here by
deciding all three together; the letter is not, and deliberately: `FittedTransform`
subclasses ADR-0038's `TrainableNode` because the lifecycle IS identical — fit and
persist, or restore a pinned state — and A's base encodes ONLY that lifecycle, not
model-ness. Writing a second mode dispatch for the selector would be the duplication
both drafts exist to remove. A selector is therefore a fitted transform that happens
to select, and inherits A's structural conformance bar for free.

**Decision.** `FeatureSelector(FittedTransform)` in `dskit/pipeline/fitted.py`,
abstract, role `fitted_transform`.

- **ONE library-agnostic hook:** `surviving_features(rows, params) -> names`. That
  is the whole extension contract. The base owns everything else — fitting on the
  declared split, persisting, projecting, and the metrics — so a pack supplies a
  selection RULE and nothing else.
- **The fitted state IS the surviving column list**, JSON-able by construction,
  which is what makes this family member cheap: `apply_state` projects each row to
  those columns and is pure and row-independent, exactly as C's purity screen
  requires. The base implements it once; no subclass writes it.
- **The list is an ARTIFACT, not just projected rows.** Serving must consume the
  identical columns in the identical order, so the state is written to the sidecar
  and restored under load mode — the same mechanism that stops a serving loop from
  re-deriving what training decided. `metrics` carries `n_candidates` and
  `n_selected`.
- **Leakage refusal is inherited, not restated.** `fit_split` is C's knob with C's
  rules: required under train mode, must name a declared split, refused at plan when
  the document declares no splits at all, and checked against the sidecar under
  load. The selector adds no rule of its own, which is the point of putting it in
  the family.
- **The sklearn pack supplies selectors BY IMPORT PATH** — `SelectKBest`, `RFE`,
  `SelectFromModel`, `VarianceThreshold`, mutual-information — through the doorway
  pattern `SklearnFit` already establishes ("the estimator is named by the
  document"). **Never a registry of wrapper classes**: that would be ~5 classes
  re-doing what the doorway does and 5 new places to drift, the same argument that
  killed per-model wrappers. Estimator paths use the DOTTED spelling the pack
  already validates.
- **The torch pack supplies importance-from-a-fitted-net through the SAME hook**,
  so a deep model's notion of importance is a selection rule like any other and
  composes with everything below.

**The three owner flows are document edits over ONE node.** This is the design
target — not three code paths:

1. *One feature set, sweep models*: selector upstream of the model; space over
   `model.estimator`.
2. *Per model, select then score*: same graph, space over BOTH keys — the search
   enumerates the pair, which `hpo-grid` already does across multiple space keys.
3. *Select once by a stated method, then sweep*: selector upstream with fixed
   params; space over `model.estimator` only.

Because the selector's method and params are ordinary declared knobs on a
searchable role, all three fall out of where the node sits and what the space
covers.

**Consequences.** Existing identity hashes unmoved: this adds a role member, kinds
and packs' selectors, and changes no existing document, param tuple or default.
Feature selection becomes declarative and leak-refusing by construction rather than
by reviewer diligence, and the selected columns become a first-class artifact, which
is what lets serving and training agree. The toolkit gains the capability the
pycaret-shaped ask wanted without adopting a framework that owns its own pipeline —
the reason that was ruled out is unchanged: a second, opaque process declaration
inside a document that is supposed to BE the declaration, with internals outside the
identity hash. Costs: one more member of a family whose purity obligation the base
can screen but not prove, and the selection rules' numerics become the toolkit's to
maintain. **Deferred:** selection over anything but named columns (interactions,
embeddings), and any selector whose state is not JSON-able — both would reopen C's
state contract rather than extend this one.

## ADR-0043 — HPO × walk-forward: per-fold re-tune measures, the plain run ships

**Status:** accepted (2026-08-28; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)

**Context.** Every fold runs through `run_document` (`driver.py:1329`), so a fold
carrying a search node builds its own `_SearchSeam`, re-tunes independently and
applies that fold's winner downstream (`driver.py:979-1003`). Mechanically
supported, semantically undecided, untested. TODO.md calls this "defensible
nested CV"; **that label is wrong.** A fold's evaluation window IS its val split
(`driver.py:1198-1200`), the planner forces every search objective onto a
`split: "val"` score node (`planner.py:536-546`), and folds refuse a cal band
(`driver.py:1271-1282`) — no outer band de-biases the fold's score. Winners may
differ per fold and nothing surfaces it: the summary aggregates scores only.

**Decision.** Keep both paths. The seam already distinguishes them, so no new run
mechanism, no `search_mode` knob, no freeze section.

1. **Per-fold re-tune stands, as MEASUREMENT.** It is the rolling-origin
   performance of the *tuning procedure*, not an unbiased estimate of a tuned
   model. Valid for comparing procedures under one fold plan; never a deployment
   estimate. Its cost is folds × (one pass + executed trials + one winner pass),
   counted and reported, never predicted.
2. **Shipping is the plain `run`.** One search; every downstream node, persisting
   trainers included, consumes the winner pass. Freezing a winner means EDITING
   the document — pin the values, drop the search node — which moves its hash BY
   DESIGN, because a different computation is a different identity. That is the
   whole mechanism; a `freeze` knob would be a second way to say it.
3. **A run surfaces its search.** The existing per-node `search_meta` is carried
   onto the run result as one node-keyed dict, so K>1 search nodes stay
   distinguishable: trials executed, and the winner and its score WHEN the kind
   produced them. Presence, not value, distinguishes "no winner produced" from "a
   winner of `None`" — a search kind is not obliged to emit one. A winner that is
   not JSON-legal is recorded as dropped rather than coerced; population happens
   before the winner is applied, so a winner-flip refusal still reports the winner
   that caused it.
4. **The walk-forward summary carries it**, on the fold row and in the aggregate:
   per search node, how many folds reported a winner and how many DISTINCT winners
   there were. Emitted only when non-empty, so an HPO-free summary stays
   byte-identical. This is the point of the ADR — per-fold winner instability
   becomes a printed diagnostic instead of folklore.
5. **Tests pin it** in `tests/pipeline/test_walkforward.py`, which today contains
   no HPO at all: differing per-fold winners with a distinct-winner count, each
   winner state (produced / absent / dropped), both winner-failure paths, and the
   absence of the section when no search node exists. Implementation lands as C7.

**Consequences.** Zero identity movement, provably: no config surface changes —
the walk-forward spec, the search kinds' params and the fold-document derivation
are untouched, and the hash reads canonical config JSON only. Summaries, node
records and result objects are run outputs, never hash material. Shipping by
pinning moves the edited document's hash, which is the intended signal. The engine
gains no mode branch, no extra plan call and no new validation surface.
**Deferred:** a fold-internal outer band (an ADR-0034-shaped inner-val/outer-eval
carve) is the only route to an unbiased tuned-pipeline estimate. Not C7.

## ADR-0044 — Searchability of a fitted transform: per-KNOB, not per-role

**Status:** accepted (2026-08-29 — owner: do A, then the rest)

**Context.** Two accepted ADRs from the same round disagree on one point.
ADR-0042 names three owner flows and says all three "fall out" of where the
selector node sits and what the space covers, "because the selector's method and
params are ordinary declared knobs on a searchable role". Flow 2 — *per model,
select then score* — is a space over BOTH keys: `model.estimator` AND the
selector's own method/params. ADR-0040 shipped `planner._UNSEARCHABLE_ROLES` with
an entry for the whole `fitted_transform` ROLE, so a space over `select.top_k`
refuses at plan and flow 2 cannot run. Flows 1 and 3 are implemented and run
end to end (`tests/pipeline_libs/test_sklearn.py`).

The refusal's own stated rationale is entirely about the FAMILY BASE's three
knobs: "every knob this family's base declares re-aims it (fit_split directly,
purity_check by switching the screen off, order_field by re-cutting which rows
fall where), and a trial's override is never plan-checked". That argument is
exact and must not be weakened. It does not, however, reach a MEMBER's own knob:
`select.top_k` or `select.selector` changes what the rule DECIDES, not which
rows it learned from — the same kind of knob as `model.estimator`, which is
searchable.

**Decision.** Narrow the entry from per-role to per-KNOB: refuse a space key
addressing a name in `FittedTransform._PARAMS` on ANY `fitted_transform` node
(the head param, so `fit_split.x` is refused too), and allow a member's own
params. The forbidden set is read from that tuple, not restated — a family
base that gains a fourth leakage knob must not need a planner edit to
protect it. Pins: `tests/pipeline/test_selector.py` (flow 2 plans;
`fit_split` / `fit_split.x` still refuse) and
`tests/pipeline/test_planner.py` (the three base knobs still refuse).
