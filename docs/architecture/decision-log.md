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

## ADR-0045 — Batched eval for the torch pack (`loader.eval_batch_size`)

**Status:** accepted (2026-08-30 — owner: config-driven, tunable)

**Context.** Training already batches via `loader.batch_size`, but
`_final_loss` (and `_score_epoch`) still select the WHOLE split in one
forward. On the live store that peak cost ~11.9 GB for ~900k rows — the
ADR-0037 twin, measured on the walk-forward backtest. Capstone only
stayed in ~8 GB by bounding the fit window.

**Decision.** Add `eval_batch_size` to the `loader` block (default-deny
inside, same as `batch_size`/`shuffle`/`seed`). When omitted it equals
`batch_size`. Both `_final_loss` and `_score_epoch` walk the split in
chunks of that size; the reported loss is the example-weighted mean of
batch means, so an MSE (mean-reduced) objective stays a true mean over
the split. Identity is untouched: the knob is optional and documents
that omit it keep their hash.

**Consequences.** Large fits stay memory-bound by the declared eval
chunk, not by `n_train`. Tunable independent of the gradient batch when
a document wants a larger eval chunk. No whole-split escape hatch —
set `eval_batch_size` to the split length if a single forward is wanted.
Assumes a **mean-reduced** objective (the pack default); a
`reduction="sum"` loss would need its own weighting. Val scoring is ONE
batched pass (loss + beliefs), not two.

## ADR-0046 — OAuth refresh and recurring market pulls belong to onboarding

**Status:** accepted (2026-08-30; owner ratified for next-session implementation)

**Context.** The intraday-equities child needs free Alpaca SIP history and
live Schwab bars. `RestApiConnector` accepts one static credential; it cannot
refresh OAuth tokens or run recurring acquisitions. `intraday_poc` already
carries a reusable Alpaca-bars implementation. Copying either mechanism into
another child would schedule drift and violate ADR-0021.

**Decision proposed.**

1. Add a generic OAuth2 refresh-token service to onboarding. Config names the
   client-id, client-secret, callback-URL and token-path environment variables;
   secret values and token material never enter configs, snapshots, logs or
   hashes. Initial browser authorization remains an explicit manual command.
   Refresh writes are atomic and the token file must be owner-readable only.
2. Add tier-2 Alpaca and Schwab connector packs. Alpaca owns historical
   one-minute SIP bars. Schwab polls its price-history REST endpoint for closed
   one-minute bars and emits the same provider-neutral schema. Vendor SDK
   imports remain inside connector verbs. Existing `intraday_poc` imports stay
   compatible through a thin subclass/re-export over the Alpaca pack.
3. Add an onboarding `watch` command that invokes ordinary finite acquisitions
   on a declared interval for one source and stream, committing one WORM
   snapshot at a time. It stops on the first error and never hides gaps.
4. Alpaca and Schwab stay distinct sources. Their normalized rows meet only
   through pipeline nodes; raw evidence is never rewritten into a false
   single-vendor history.
5. Phase 1 ships REST bars only. Historical quotes and Schwab Level One
   streaming are a later, separately designed and validated extension.

**Consequences.** The new child contains vendor policy and configuration, not
auth, retry, token or polling plumbing. Initial authorization still needs the
operator. Package READMEs/CLAUDE trees and connector conformance tests must
land with implementation.

## ADR-0047 — Action cadence is an event-time grid, independent of label horizon

**Status:** accepted (2026-08-30; owner ratified for next-session implementation)

**Context.** `ReturnWindows.label_lead` can label 1/5/15/30/60-minute returns,
but it does not make a strategy act at those intervals. Scoring every minute
with a 60-minute label is not a 60-minute strategy. Sequence stride is also
wrong: a missing bar shifts every later decision.

**Decision proposed.** Add one domain-neutral transform kind, `event-grid`, in
the pipeline flow family. It preserves stream order and retains records whose
standard `asof_ms` satisfies
`(asof_ms - offset_ms) % period_ms == 0`. Both params are declared, validated
integers (`period_ms > 0`, `0 <= offset_ms < period_ms`). Session filtering and
the choice of offset remain child configuration. Training, backtesting and
serving instantiate the same node from the run document.

Each intraday-equities action document sets `label_lead` and `period_ms` to the
same 1/5/15/30/60-minute candidate initially. A child test pins that agreement;
later research may deliberately decouple them in a new document.

**Consequences.** Horizon and action cadence become separately measurable and
missing minutes cannot move the clock grid. The kind is useful outside finance,
adds no scheduler, and introduces no mode branch.

---

## ADR-0048 — Release spent node outputs after their last consumer

**Status:** accepted (2026-08-30; owner directed)

**Context.** EXECUTE kept every node's ports until RECORD. A cadence document
then held the raw 1-minute tape, the windowed tape, and the grid at once.
RECORD's `_carryable` also `json.dumps` every port to see if it fit in 20k
characters, so an 8.9M-row list died after the model had already fitted.

**Decision.** After each successful node, replace a port with `_summarize`
when every `$` reader has run, no not-yet-run search still needs it as a
clean ancestor, and the value is a record stream (`len >= 256` or too big
to carry). `_carryable` refuses those streams without dumping. Summaries
stay out of `carry.json`. The pinned instance is dropped so a memoized
scan can be collected. `flags` is never released.

**Consequences.** Small synthetic outputs and JSON-small state stay on
`DocumentRunResult`. Search trials still see reserved ancestors. A terminal
port that is itself a huge list is still held until RECORD, but carry no
longer dumps it.

---

## ADR-0049 — Direct multi-horizon output (`n_ahead`) on the zoo and sklearn

**Status:** accepted (2026-08-30; owner directed)

**Context.** ADR-0041 deferred any output width other than `(B, 1)`. The
intraday child now needs a path of 1-minute forecasts out to a scan-chosen
H. That is generic: any predictor that can emit one step can emit H.

**Decision.**

1. **`n_ahead` (int >= 1, default 1)** on `torch-ts-train` and on
   `ReturnWindows`. Omit it and every existing document is unchanged.
2. **Torch zoo:** each arch's last linear maps to `n_ahead`. `label` may
   be one key or a list of `n_ahead` keys. `torch.py` `_feature_problems` /
   `_usable_rows` accept that list; MSE still flattens `(B, H)`. Binary
   head + `n_ahead > 1` refuses. A tiny `transformer` arch ships (one
   encoder layer).
3. **Sklearn:** `label` may be a list; the doorway wraps
   `sklearn.multioutput.MultiOutputRegressor` around the named estimator.
4. **Windows:** `n_ahead > 1` emits `y_ahead_1` … `y_ahead_H` at
   `k * label_lead` tape steps. `n_ahead == 1` still emits `label_name`.

**Consequences.** Existing hashes stay put (the knob is omitted). The
`(B, 1)` contract is the default, not the ceiling. Path scoring is path
MSE / per-lead IC; the one-pick program still wants a scalar — last-step
or a later document. `torch.py` identity pin moves on purpose (label list).

---

## ADR-0050 — Bounded / sliding train windows (`train_start_ms`)

**Status:** accepted (2026-08-31; owner directed)

**Context.** I-223 refused any `train_days != "all-prior"`: `TimeSplitConfig`
had no train-start cut, so a bounded window could not be expressed. Walk-forward
v1 was expanding-only (ADR-0027). The holistic train run needs a sliding
window of length T and a T bakeoff `{1y, 2y, 3y, 5y, all-prior}`.

**Decision.**

1. Optional `train_start_ms` on `TimeSplitConfig`. Omitted when unset — existing
   hashes unmoved. `split_of`: `t < train_start_ms` → no split; else unchanged.
2. `TrailingSplitSpec.materialize` with integer `train_days` stamps
   `train_start_ms = train_end_ms - train_days·DAY + 1` (exactly `train_days`
   daily stamps, cal-band boundary rule). I-223 refusal dies.
3. `WalkForwardSpec.train_days` (int or `"all-prior"`, default `"all-prior"`,
   omitted when default). Each fold's train is `[cutoff - T, cutoff - embargo)`.

**Consequences.** Sliding and expanding are both declared. `$splits.train_start_ms`
appears exactly when bounded. A T bakeoff is a document edit of `train_days`.

---

## ADR-0051 — `tft` architecture in the torch_ts zoo

**Status:** accepted (2026-08-31; owner directed)

**Context.** TFT matches mixed static / known-future / observed-past panels.
The zoo contract is `(B, seq, ch) → (B, n_ahead)`. pytorch-forecasting is not
a dependency. Foundation models (TimesFM / Chronos / Moirai) are deferred.

**Decision.** Register `tft` in `torch_ts.py`. Compact TFT-lite over the existing
contract: variable-selection over channels, LSTM encoder, gated attention,
linear head to `n_ahead`. Nets stay inside `build_module`. Defaults:
`hidden_size` 16, `nhead` 2, `dropout` 0.1. No new collate, no extra extra.

**Consequences.** `space: {"model.arch": [..., "tft"]}` sweeps it. A richer
static/future collate would be a later ADR.

---

## ADR-0052 — Top-quantile reseed ensemble from a search ledger

**Status:** accepted (2026-08-31; owner directed)

**Context.** One HPO pass (≈50 TPE trials) should not ship a single winner.
The ensemble is E retrains drawn from the top `frac` of trials with fresh
seeds, trained to completion. In-trial `seeds` (mean score) is a different
contract and stays.

**Decision.** New transform kind `top-trials`: `trials` port + `frac` + `size`
+ `seed`. Rank by score (`select` min/max), keep `ceil(frac · n)` (at least 1),
sample `size` members with replacement, assign distinct seeds. Output `members`
(`overrides`, `seed`). Shipping is a second document (foreach over members,
full epochs). The search node's single winner pass is unchanged (ADR-0043).

**Consequences.** HPO stays one search. Ensemble diversity is config, not a
driver mode. `frac`/`size`/`seed` live in JSON.

---

## ADR-0053 — Declared recency weights on walk-forward aggregates

**Status:** accepted (2026-08-31; owner directed)

**Context.** Model pick is `argmin_m Σ_k w_k VL_{m,k}`. Searching `w_k` with
the models overfits the fold mix. Equal mean is what ships today.

**Decision.** Optional `weight_halflife_folds` (int ≥ 1) on `WalkForwardSpec`.
Omitted → equal mean, summaries byte-identical. Set → `w_k = 0.5^((K-1-k)/h)`
on scored folds, renormalized; aggregate gains `weighted_mean` (emitted only
then). Not searchable.

**Consequences.** Recency is a declared experiment knob. Equal-weight docs do
not move.

---

## ADR-0054 — Pinball metric and torch `patience`

**Status:** accepted (2026-08-31; owner directed)

**Context.** OL/VL include pinball; DL HPO includes patience. Torch runs every
epoch and keeps the monitor's best (ADR-0035) but never stops. `metrics.py`
has MSE/MAE, not pinball.

**Decision.**

1. `pinball(q, y)` in `metrics.py` (τ = 0.5). Torch pack adds `pinball_loss`
   (imported only inside the callable); `loss_params.tau` in (0, 1).
2. Optional `patience` (int ≥ 1) on `TorchTrain`. Requires `monitor`. Omitted
   → run all epochs. Set → stop after `patience` epochs without a new best;
   restore still happens.

**Consequences.** Hashes unmoved (both knobs omitted when absent). `torch.py`
content pin moves on purpose.

---

## ADR-0055 — Intraday H/L/T/V training framework

**Status:** accepted (2026-08-31; owner directed)

**Context.** Cadence twins (1/5/15/30/60) and a 1165 top-k IC row are not a
training lock. H, L, T, V, keep-set, holdouts, and ensemble need one table.

**Decision.** Child lock is
`children/intraday_equities/docs/decisioning/framework.md`. H = LightGBM on the
session set. L from JSON floor 30. T bakeoff after H/L. V uses H-length embargo
and 36–48 folds. Recency, bounded train, TFT-lite, pinball/patience, and
top-quantile ensemble are ADR-0050…0054. Test B (August 2026) is unassigned.
`universe.lookback` does not move until a decisioning row.

**Consequences.** Pipeline #1 is `run-hl-scan.json`. Pipeline #2 pins H/L/keep
then T + 50 TPE + `top-trials`. Action documents stay; they are not the lock.

---

## ADR-0056 — Child action journal (`dskit.journal`)

**Status:** accepted (2026-09-01; owner directed)

**Context.** A child today keeps a hand-edited `docs/decisioning/` grid plus
one markdown file per lock. That records *decisions*, not *actions*. Acquire,
research, pipeline runs, and live loops leave no single chain. The owner
wants every action labeled automatically (argv + timestamp), two tables
(full ledger; owner-selected path to production), and markdown that is
readable on GitHub without being the write format.

**Decision.**

1. Fourth toolkit package `dskit.journal`. Stdlib-only. Never imports
   pipeline, onboarding, or assets. Categories (closed): `acquire` |
   `research` | `execute` | `production`. Validate/certify/publish fold
   into `acquire`; bakeoffs/HPO/walk-forward fold into `execute`.
2. Per child, `journal.json` is the marker. Store is CSV (git-friendly
   edits); `docs/decisioning/README.md` is **generated** from it (two
   tables). Operators read the markdown; writers append CSV.
   - `actions.csv`: `id, category, step, executed_at, inputs, outputs,
     db_location, notes`. `inputs` holds argv. IDs are `A0001`… monotonic.
   - `path.csv`: `id, criteria` only (`empirical` | `judgemental` |
     `n/a`). Render JOINs category/step/db from actions so they cannot
     drift. Hooks never write `path.csv` — only `journal promote`.
3. Hooks (automatic; pytest is a no-op via `PYTEST_CURRENT_TEST`):
   pipeline RECORD and onboarding verbs function-import `dskit.journal`
   (purity allowlists this one sibling, same shape as onboarding→assets).
   Research: `journal research` writes `docs/research/<slug>.md` + a row.
   Production: `journal.production()` context manager around `live.main`
   (one row per process, not per tick).
4. Locate: walk up from cwd. `journal.json` found → record there.
   `pyproject.toml` + `configs/` without `journal.json` → **refuse**.
   Neither → no-op (toolkit tests). `DSKIT_JOURNAL_ROOT` overrides.
5. Cross-document pipeline chaining is **out of scope** (`$prev` stays
   same-document). Evidence markdown under `docs/decisioning/` stays;
   the grid is replaced by the generated tables.
6. Skeleton ships the layout. Existing children are initialized in the
   same change (retrospective rows, notes say artifacts may be incomplete).
   Project skill `refresh-child-journal` brings drifted copies forward.

**Contents (package).**

```
dskit/journal/
  __init__.py     public surface
  __main__.py     init | record | research | promote | render | exec
  base.py         errors, UTC, csv headers
  model.py        Action / PathRow; closed vocabs; default-deny
  locate.py       walk-up + uninitialized-child refusal
  store.py        atomic CSV append / path mutate
  render.py       CSV → docs/decisioning/README.md
  record.py       append_action; pytest skip
  hooks.py        record_execute / record_acquire / production()
  research.py     docs/research/<slug>.md template + row
  README.md / CLAUDE.md
tests/journal/    purity + model/store/render/locate/record/hooks/cli
```

**Consequences.** A child without `journal init` cannot acquire, run, or
go live. Path-to-production is an owner act. Journal failure on a hooked
path refuses the parent command (unlike tracking sinks, which swallow).

---

## ADR-0057 — No-information test vs the mean (Clark–West + sequential h*)

**Status:** accepted (2026-09-01; owner directed)

**Context.** Maximum informative horizon is not “farthest |IC| within 1 SE.”
Breitung–Knüppel (2021) define h* by a **no-information** null under quadratic
loss: the forecast’s MSPE is no better than the unconditional mean. Nested
mean comparisons need Clark–West (2007), not naive DM; overlapping horizons
need Newey–West lag in **observation steps**. ADR-0033 closed `stat_test`
`METHODS` on purpose — this is a different estimand (one series of paired
forecast errors, not a per-instrument cluster bootstrap of trading
improvements).

**Decision.** Primitives in `dskit/pipeline/stats.py` (stdlib, no new kind,
`METHODS` unchanged):

1. `clark_west_series(y, yhat, mu)` — MSPE-adjusted loss gap
   `(y-μ)² - (y-ŷ)² + (ŷ-μ)²`. Feed `cluster_bootstrap_t` when the
   independence unit is a cluster (a day), not a row.
2. `newey_west_mean(values, lags)` — HAC mean, Bartlett weights, lag < n.
   One-sided H1: mean > 0. `lags` is overlap in **steps** (the caller maps
   a clock horizon to `max(steps-1, 0)`).
3. `no_information_test(y, yhat, mu=None, lags=0)` — left/right MSPE plus
   Clark–West t. Omitted `mu` is the mean of **this** `y` (descriptive);
   pass a train mean for a true benchmark. One time-ordered series; a
   panel is the caller’s to collapse or test per unit.
4. `max_informative_horizon(ordered, alpha=0.05)` — BK walk: first
   non-rejection stops; h* is the last rejected horizon (`None` if the
   first fails). Fixed α (a test sequence, not a consistent selector).
   Monotonicity is the caller’s assumption; the walk does not check it.

**Consequences.** Children import the functions; HorizonScan is unchanged
until a child document wires them. No config-hash movement.

---

## ADR-0058 — H and L from sliding CV through Nov 2025; HPO through Feb; nothing after

**Status:** accepted (2026-09-01; owner directed; revised same day; per-series H then pooled ŷ + category 2026-09-01)

**Context.** ADR-0055 locked H from LightGBM |IC| on Dec 2025–Feb 2026 val,
then L and TPE reused that window. Owner: H and L from **one sliding
walk-forward through 2025-11-30**; HPO may use through Feb 2026; nothing
after 2026-02-28 is peeked.

**Decision.** Child lock:
`children/intraday_equities/docs/decisioning/hstar-go.md`.

- Walk-forward (ADR-0027/0050): `first=2019-01-07`, `step_days=63`,
  `count=40`, `val_days=63`, `embargo_days=5` (`lead_stop`, not H-length),
  `train_days=730` (2y). Last val ends 2025-11-30. Same folds for H and L.
  CV LightGBM: one pooled tree; symbol is a category. Short inner-train
  HPO (`hpo_trials=8`) hashed on the document; fold val is unread.
  Features: 46 non-lag session fields + 20 momentum/vol (no `ret_lag_*`).
- H: per-fold pooled LightGBM then no-information `h*` **per tradable
  name** (ADR-0057). Inner HPO on train only. A name GO’s iff the
  contiguous reject run starts at `h=5`; H is that run’s far end.
  Clock-mean pooling is out. How five H’s become a book lock (map / min
  / median) is deferred (`docs/adhoc/deferred_decisions.md`).
- L: pass 2 on those folds at locked H*; 1-SE shortest of mean fold MSPE.
- HPO: val 2025-12-02 → 2026-02-28, H/L/keep frozen. T bakeoff lives here.
  Refit winner through 2026-02-28.
- Untouched: 2026-03-01 →. Confirm Mar–May; backtest Jun–Aug (incl. Test B).
- ADR-0055 |IC| H=470 is not the estimand. CV documents set
  `test_end_ms` to 2026-02-28.

**Consequences.** Next execute is
`configs/run-hstar-cv-series.json` (2y slide, one tree, per-series H).
Not the aborted clock-mean `run-hstar-cv.json`. Not a Mar–May single split
and not T bakeoff first. Do not write `label_lead` until the book-H
decision closes.



---

## ADR-0059 — The scan's label: one definition, optionally vol-normalised and market-residual

**Status:** proposed (2026-09-03; owner directed the two transforms in
session; the DEFAULT is unchanged, so nothing already recorded moves)

**Context.** Every label in `NoInformationScan` is `log(px[t+h]/px[t])` on
the 1-minute RTH tape, written out THREE times (`_scan_fold`,
`_scan_fold_stamped`, and through them the h\* walk) — the exact
"value in two places with nothing pinning them" shape. Two defects follow
from the label itself, not from the estimator:

1. **Heteroskedastic weighting.** Squared loss weights a row by the
   variance of its label, so the open hour (≈5–10x midday variance)
   dominates the fit; A0040's ridge underfits the whole day to survive
   the open. Pooled IC over one name ranks ACROSS time, so the ranks
   partly encode minute-of-day rather than signal.
2. **Unforecastable market component.** At 5–20 minutes most of a single
   name's variance is the market move. It enters the label as noise the
   features (which already carry `residual_SPY`) are not asked to
   explain.

**Decision.** One label object, `_LeadLabel`, built once per run from the
tape arrays and passed into both fold builders; `_raw_lead_return` is the
single definition of the raw log return. Two declared, default-off knobs
on `NoInformationScan` (default-deny params, emitted only when present,
so every recorded document's identity hash is unmoved):

- `label_scale`: `"raw"` (default) or `"vol"` — divide by
  `sigma_t * sqrt(h)`, `sigma_t` = causal `rolling_std` of the 1-minute
  log return over `vol_window_minutes` (default 390, one RTH session).
  Equivalent to WLS on the raw return; the estimand becomes a
  risk-adjusted return, which is what inverse-vol sizing trades.
- `label_residual`: `null` (default) or a reference symbol on the tape
  (e.g. `"SPY"`) — subtract `beta_t * y_ref(t, t+h)`, `beta_t` =
  `rolling_sum(r*r_ref) / rolling_sum(r_ref^2)` over
  `beta_window_minutes` (default 3900, ten sessions).
- `vol_floor` guards a stale-tape divisor. Composable: residual first,
  then the vol of the RESIDUAL return scales it.

Causality: `sigma_t` and `beta_t` read only bars at or before `t`; the
label reads `t -> t+h`. Session boundaries are blanked (a gap over twice
`period_ms` is not a 1-minute return), so an overnight jump never enters
`sigma` or `beta`. A reference symbol absent from the tape is a loud
refusal, never a silent NaN column.

**Consequences.** `train_mspe`/`val_mspe` are in LABEL units — a
vol-normalised run's MSPE is NOT comparable with a raw run's, and the
Clark–West / no-information verdict is a different estimand (it tests a
risk-adjusted forecast). `val_ic` stays rank-based and comparable.
Existing configs declare neither knob and are byte-identical. Reading
these labels as evidence requires a fresh fold sequence; A0040's numbers
do not carry over.

---

## ADR-0060 — The scan's estimator is a document knob; its t and SE are printed

**Status:** proposed (2026-09-03; owner asked for a ridge-vs-LightGBM
comparison on one cohort, with per-fold t and p)

**Context.** `NoInformationScan` read `scan.estimator` from the UNIVERSE
document only, so comparing two models meant two universe files — and a
universe file states the cohort, the session, the grid and the horizon.
Two of them differing in one string is the cohort restated to say one
thing, which `test_run_docs_do_not_restate_the_cohort` exists to stop.
Separately, each lead's row carried the Clark–West `p_value` but not the
`t` or `se` behind it, so a reader could not tell a p of 0.30 driven by a
small gap from one driven by a wide HAC band.

**Decision.** `estimator` joins `estimator_params` as a node knob that
overrides the universe's `scan` block (default-deny; emitted only when
declared, so no recorded document's hash moves). The per-lead record
gains `t_stat` and `se`, the walk's per-series metrics gain
`t_stat_<symbol>`, and each series logs `h*`, `p` and `t` at INFO so a
running walk is readable live.

**Consequences.** One universe serves a model bakeoff; the model that ran
is in the run document, where the identity hash already covers it. Curve
rows are two keys wider — a records shape, not an identity.

---

## ADR-0061 — The zoo reaches sklearn-shaped callers: `ZooEstimator`

**Status:** proposed (2026-09-03; owner directed a TFT/GRU/LSTM look after
ridge and LightGBM both landed at the null)

**Context.** ADR-0041's architecture zoo is reachable only as the
`TimeSeriesTrain`/`TimeSeriesPredict` node pair, which own an artifact
protocol and a document shape. Every evaluation that matters here —
per-fold Clark–West `t` and `p`, the `h*` walk, walk-forward folds — lives
inside a scorer that fits `scan.estimator` through the sklearn contract
(`cls(**params)`, `fit(X, y)`, `predict(X)`). So a sequence model could
not be compared with ridge and LightGBM on identical folds without either
re-implementing the evaluation beside the zoo, or re-implementing the zoo
beside the evaluation. Both are the same defect.

**Decision.** `ZooEstimator` joins `libs/torch_ts.py` — an sklearn-shaped
façade over the SAME `_ARCHS` registry, so an arch is defined once and
reachable two ways:

- Constructor knobs are the zoo's (`arch`, `arch_params`, `order`) plus a
  training block (`epochs`, `lr`, `batch_size`, `weight_decay`, `seed`,
  `device`, `standardize`) — default-deny, defaults from the registry.
- The flat feature row splits by NAME, not by position: columns matching
  `sequence_prefix` (default `ret_lag_`) become the time axis, ordered by
  their integer suffix; every other column is a static covariate,
  broadcast as a constant channel over that axis. The estimator therefore
  sees `(B, seq_len, 1 + n_static)` — a real path plus context, not a
  feature vector pretending to be a sequence. The broadcast is a view
  (`expand`), never a materialized copy.
- `fit(X, y, feature_names=None)`. A caller that has names passes them;
  one that does not gets a single-channel window over the whole row.
  `_fit_estimator` passes them exactly the way it already passes
  `categorical_feature`: only when the callee's signature declares it.
- Standardization is the estimator's own (train mean/sd, applied to
  predict). A torch model on unstandardized columns fails the way
  A0040's ridge did.

**Consequences.** One registry, two doorways: an arch added for the node
pair is immediately comparable against ridge and LightGBM on the same
folds, and any sklearn-shaped caller in any project can reach it. The
façade does NOT write artifacts — it is an in-process estimator, so
serving still belongs to the node pair. Torch stays inside methods (the
purity gate), the wrapper `nn.Module` is defined inside `fit`.

**Pre-registered bar for the run this unlocks** (owner asked for the
comparison; the criterion is declared BEFORE it runs, so the result is
readable either way): an architecture is worth pursuing only if it clears
BOTH (a) more than 10% of the 60 name-folds at `p < .05` — ridge managed
6.7%, LightGBM 5.0%, and the null delivers 5% — and (b) a mean validation
MSPE below ridge's 1.345. Anything less is the null with more parameters.

---

## ADR-0062 — The lead grid is a document knob, not a cohort fact

**Status:** proposed (2026-09-03; owner asked for the same five-model
comparison at H = 1, 30 and 60 minutes)

**Context.** `horizon.lead_start/lead_step/lead_stop` live in the UNIVERSE
document, so changing the horizon meant a new universe file — and a
universe file states the cohort, the session policy, the feature grid and
the holdout calendar. Three of them differing only in a lead is the
cohort restated three times, the defect ADR-0060 removed for `estimator`
and `test_run_docs_do_not_restate_the_cohort` exists to catch. The
horizon is a property of the QUESTION a run asks, not of the names it
asks it about.

**Decision.** `lead_start`, `lead_step` and `lead_stop` join
`NoInformationScan`'s knobs, overriding the universe's `horizon` block
when declared (default-deny, emitted only when present, so no recorded
document's hash moves). `lead_start` remains the TRAINING label as well
as the grid's first point — a run that declares `lead_start == lead_stop`
asks about exactly one horizon, which is what "run this at H = 30" means.
The HAC lag stays `lead // period_minutes - 1`, so a longer lead widens
the Newey–West band by itself; nothing about the overlap correction is
per-document.

**Consequences.** One universe serves every horizon. A run's identity
hash covers the lead it asked about, so two horizons can never collide in
one run series. `universe.json`'s `horizon` block stays the default for
documents that declare nothing.

---

## ADR-0063 — Two price scales, two sources; and the data cut is a fetch bound

**Status:** proposed (2026-09-03; P9 found both splits inside the history,
and the re-pull needed somewhere to put the study's hard cut)

**Context.** `alpaca-sip` was registered with `adjustment: "raw"` — the
connector's default — for one stated reason: the Schwab live overlap
compares like with like only on the as-traded scale. That reason still
holds, and it is unrelated to what a model is fit on. Raw is unadjusted
for corporate actions, so AAPL's 2020-08-31 4-for-1 and WMT's 2024-02-26
3-for-1 sit in the tape as one-minute returns of log(1/4) and log(1/3):
changes of unit read as price moves, roughly eight times larger than the
largest genuine day in ten years. Every run so far handled this by
EXCLUDING both names with a `names` filter, which is why the horizon
sweep decided on three stocks. Separately, the study's rule is that no
data after 2026-02-28 is read, and the connector had no way to say so: a
backfill window always ended at "now", so the cut could only be enforced
by trimming after the bytes were already on disk.

**Decision.** Two sources, not one adjustment knob flipped in place.
`alpaca-sip-split` (`configs/source-alpaca-split-backfill.json`) declares
`adjustment: "split"` and carries the twelve symbols the feature work
needs; `alpaca-sip` keeps `adjustment: "raw"` and its overlap job.
Observations are keyed by source name, so the two trees never mix and a
raw-versus-adjusted comparison stays available. `adjustment` is DECLARED
in both, never defaulted, and the connector's spec now says in words that
its default is unadjusted. Split only, not dividend: a split is a change
of unit and must be removed, while an ex-dividend fall is a price move
the short-horizon label is supposed to see — and dividend adjustment
rescales the whole prior series at every ex-date, so no two pulls of one
history would agree. The Alpaca pack gains one optional knob, `end`: an
exclusive ISO upper bound on the fetch window, clamped against the "now
minus SIP lag" bound rather than replacing it. Absent, nothing changes.

**Consequences.** A study with a data cut declares the cut where the
fetch happens, so bars past it are never requested — the constraint is
enforced by the connector, not by a downstream filter someone can forget.
Back-adjustment expresses history on the pull date's share basis, so a
FUTURE split silently restates every earlier bar in this tree: the
manifest's `acquired_at` is the basis date, and the series must be
re-pulled whole rather than topped up. Documents pointing at
`alpaca-sip-split` are on a different price scale from every number
recorded before 2026-09-03, so every AAPL and WMT result to date is void
and the horizon grid has to re-run on five names. Two stores cost disk
and one more thing to keep straight; the note in each config says which
is which.

---

## ADR-0064 — A walk persists every scored row, not just the fold's mean

**Status:** proposed (2026-09-03; plan `docs/plans/2026-09-horizon-search.md`
— the P5 skill rule could only be half-applied, and P6 and P8 cannot be
applied at all, for one shared reason)

**Context.** A walk-forward fold writes summaries: an MSPE pair, a
Clark–West t, a row count. **A mean cannot be unpacked back into rows.**
So the three questions now queued all stall on the same missing artifact:

- ADR-0067's pooled Diebold–Mariano t needs `d_t`; `RESULT-P5.md` had to
  report 113 definite fails and 7 `unresolved` cells because the pooled
  half was unrecoverable from the 30 walks on disk.
- P6's calibration slope needs the `(y, ŷ)` PAIRS, and its per-timestamp
  cross-sectional IC needs the timestamp beside every pair.
- P8's scramble test needs to re-score shuffled outcomes on the same rows.

ADR-0067 began this by writing its gaps to `skill.json`. That artifact
answers exactly one question: it keeps `d_t`, from which `y`, `ŷ` and the
benchmark can never be recovered. It also builds the whole payload in
memory before serialising it, which is the wrong shape for a walk that
already holds ~11.5 GB of a 17 GB box.

**Decision.** Every scan that scores validation rows PERSISTS THEM, and
the row — not the gap — is the stored unit.

1. **Columns**, seven, one row per scored validation row:
   `ts` (int64, ms), `series` (dictionary-encoded string), `fold`
   (int16), `horizon` (int16), `yhat`, `y`, `mu` (float32). `mu` is the
   fold's CONSTANT training-mean benchmark — a property of the training
   window, not recoverable from validation rows, so it is stored per row
   rather than re-derived.
2. **Format and place:** parquet, ONE FILE PER FOLD, at
   `<fold>/artifacts/<node>/predictions.parquet`
   (`predictions.PREDICTIONS_FILE`). Per fold, not per run: a fold is the
   unit that is written, so a per-fold file needs no cross-fold buffer, no
   append-to-an-open-file across runs, and no repair when a walk stops
   half way — and `walkforward.json` already orders the folds for the
   reader. Row spacing is stamped in the file metadata so no reader
   guesses the overlap an HAC band needs.
3. **Streamed, never accumulated.** One `(series, horizon)` block is
   converted, written as its own row group, and dropped. Nothing per-row
   survives the call that scored it.
4. **The fold's ordinal** rides on `NodeContext.fold_index`, supplied by
   `run_document`; a standalone run stamps `-1`. The fold document itself
   carries only its cutoff, so without this the rows could not say which
   fold produced them.
5. `runs.score_walk` reads these rows and rebuilds `d_t` and `q_f` from
   them, so ADR-0067's verdict is computed from the evidence rather than
   trusted from a summary. A pre-0064 `skill.json` still reads.
6. The store is domain-neutral (`dskit/pipeline/predictions.py`): a
   series is any string key, a horizon any integer lead, the benchmark
   any constant forecast. Nothing in it knows what is being predicted.
   pyarrow is named only inside functions, so importing a node still does
   not import parquet.

**Size, measured before building** (zstd, random floats — the pessimistic
case; real forecasts repeat and compress harder): **14.7 bytes per row.**
One fold of 3 names × 4 leads × ~3,100 validation rows = 37,200 rows =
0.55 MB. A 20-fold walk over 3 names: **11 MB**. Over 5 names (P9
restores AAPL and WMT): **18 MB**. Over 5 names at 1-minute row spacing
(the densest grid P1 proposes): **91 MB**. Against a 500 MB ceiling that
is 5x of headroom at the worst configuration we can run today, so EVERY
scored validation row is stored and none is dropped. If a future grid
crosses the ceiling, the cut is leads — the walked lead grid, not the
training lead — and it must be stated in the run document, never taken
silently.

**Memory, measured:** the first fold of a walk pays a one-time ~112 MB as
pyarrow's parquet and compression libraries load into the process (~1% of
the walk's 11.5 GB). Every fold after that adds **≤ 0.03 MB peak**,
because the writer holds one block (~3,100 rows ≈ 90 kB) at a time and
the arrays it converts are ones the scan already materialised for the
Clark–West test. This REPLACES ADR-0067's in-memory JSON payload, so the
net change to a walk's peak is a reduction after the first fold.

**Consequences.** The scan node now requires pyarrow (`dskit[parquet]`)
when it runs inside a run directory; a scan scored outside one still
scores and simply leaves no evidence. **The 30 walks already on disk are
unaffected** — they saved no rows and stay half-scored under ADR-0067
until they are re-run, which is now the only thing standing between the 7
`unresolved` cells and a verdict. P6 and P8 need no further persistence
work: the calibration slope, the per-timestamp cross-sectional IC and the
scramble null are all functions of the columns above.

---

## ADR-0065 — Row spacing, the price field and the scoring lattice are run knobs

**Status:** proposed (2026-09-03; P1 needs spacings {1,5,10} crossed with
horizons and every cell scored on the same instants, P4 needs one run
repeated on three price definitions)

**Context.** Three knobs that decide what a run MEASURES live in the
universe document, which states the cohort: `period_ms`/`offset_ms` (one
feature row every five minutes) and `price_field` (`"close"`). A universe
file also names the symbols, the session policy, the holiday calendar,
the feature scales and the holdout dates, and
`test_run_docs_do_not_restate_the_cohort` asserts that every universe
variant agrees with `universe.json` on exactly those cohort keys —
`period_ms`, `offset_ms` and `price_field` among them. So P1's grid of
five spacings and P4's three price definitions cannot be written down at
all: each cell would need its own universe file, and the test that stops
two copies of a cohort from drifting would fail on every one of them.
This is the defect ADR-0060 removed for `estimator` and ADR-0062 for the
lead grid, in the two remaining places.

P1 asks for one thing more than a knob. Rows formed every minute and rows
formed every ten minutes do not land on the same instants, so a gain at
s=1 and a gain at s=10 are not two measurements of the same quantity —
they are scored on different clocks, with different overlap, and the
per-cell HAC lag `lead // period_minutes - 1` differs by cell as well.
P1's design fixes this by scoring every cell on the 30-minute lattice,
the lowest common multiple of the spacings: the label at a lattice
instant is the same number whatever the spacing, so a difference between
cells is model-only. Density then acts where it is supposed to act — on
the TRAINING rows — and nowhere else.

**Decision.** Three parts.

1. `Universe` gains an optional `overrides` param: a dict restricted to
   `period_ms`, `offset_ms` and `price_field`, applied to the loaded spec
   before it is emitted. One declaration, read by every consumer, so the
   feature node and the scan node can never disagree about the spacing or
   the price. It is default-deny and emitted only when present, so no
   recorded document's hash moves; when present it enters the universe
   node's fingerprint, so two spacings can never collide in one run
   series. The cohort keys stay cohort keys — a universe VARIANT still
   may not move them, and the test still says so.

2. `price_field` is honoured where the price is actually lifted.
   `_symbol_ohlcv` takes the field name, so the return features, the
   emitted tape and the label all read one series and cannot drift apart.
   The accepted names are `close` (the last trade of the minute),
   `vwap` (the volume-weighted average of the minute's prints, already in
   every Alpaca bar) and `mid`, declared here and NOT present in the
   store today: the quote pull that will populate it is separate work, so
   a run that names `mid` fails loudly on an empty price series rather
   than falling back to `close` and reporting a comparison it did not
   make. Nothing here depends on that work landing.

3. `NoInformationScan` gains `score_period_ms` and `score_offset_ms`: the
   SCORING lattice, separate from the row spacing. Validation rows are
   restricted to stamps on the lattice; training rows are not. The HAC
   lag becomes `lead // lattice_minutes - 1` when a lattice is declared,
   because the overlap that matters is the overlap between SCORED rows —
   at a 30-minute lattice, labels out to h=30 do not overlap at all and
   the plain SE is right, which is the point of choosing it. The lattice
   must be a whole multiple of the run's row spacing and share its phase,
   or no row lands on it; the scan refuses with both numbers rather than
   scoring an empty fold. Inner-holdout HPO is unchanged: it selects, it
   does not score.

**Consequences.** P1's grid becomes 24 run documents against one universe
file, and P4's three arms become three documents differing in one word.
A run's identity hash covers its spacing, its price definition and its
lattice, so cells cannot collide. Two numbers now describe cadence and a
reader must keep them apart: rows are FORMED at `period_ms` and JUDGED at
`score_period_ms`; a run that declares no lattice is judged on the rows
it formed, exactly as before. Runs that share a lattice are comparable
across spacings; runs that do not declare one are comparable only with
each other. MSPE on a `vwap` run is not comparable with MSPE on a `close`
run — the underlying series differs — which is P4's whole question, and
the same caveat ADR-0059 attached to the label transforms. One feature
is left mixed on purpose: `overnight_gap` is the log of a session's
first OPEN over the previous session's last priced bar, so on a `vwap`
run it reads an open against a minute average. Open, high, low and
volume describe a bar's SHAPE and are not the priced series; moving
them would change the range, `clv` and Amihud features as well, which
is not the question P4 asks.

**Rejected.** A universe file per cell: it restates the cohort, which is
the defect. A `price_field` on each consuming node: the features node and
the scan node would each carry a copy, and a run that changed one would
silently label with one price and predict with another. ADR-0064 is
taken by the prediction-saving work in flight, so this is 0065.

---

## ADR-0066 — The study reads from 2018-01-01, and that is where XLF becomes usable

**Status:** proposed (2026-09-03; the split-adjusted re-pull left one
uncorrected corporate action, and no document said where history starts)

**Context.** The store holds 2016-01-01 to 2026-02-28. No document said
which part of it a run may read, so "from 2016" was true by default
rather than by decision. Two facts bear on it. First, the fold structure:
the walk's first validation is 2022-05-06 with a 730-day training window,
so the earliest bar any current fold reads is about 2020-05-06 — six and
a half years of the store have never entered a run and were being scanned
and held in memory regardless. Second, XLF. P9's re-pull removed every
split-sized jump on all twelve symbols, but XLF falls 18.2% on 2016-09-19
on BOTH the raw and the split-adjusted scale: that is the XLRE spin-off,
carried out partly as a 1231-for-1000 share split, and the vendor does
not adjust for it. 1000/1231 = 0.812 against the 0.818 observed, so most
of that fall is a change of unit read as a price move. It is the same
defect as the splits, one twenty-fifth the size, and at 18.2% it is the
same size as JPM's largest genuine day — no threshold separates them.

**Decision.** Runs read bars stamped 2018-01-01 or later.
`BarsFromStore` gains an optional `start_ms` — an inclusive epoch-
millisecond lower bound on the bars it emits, declared in every run
document beside the source it names. It is the mirror of the `end` bound
ADR-0063 put on the fetch: the cut is stated where the data is read, not
left to a filter someone can forget to wire.

2018-01-01 is chosen for the training window, not for XLF. It precedes
the earliest bar any current fold reads by two years and four months,
which is enough headroom to double `train_days` from 730 to 1460 without
truncating the first fold — so no fold structure changes, and none has
to change when P1 or P7 asks for a longer window. What it drops is 2016
and 2017: two years that no fold has ever read, that carry a different
microstructure, and that would only ever enter a run as the oldest and
least relevant training data.

XLF's spin-off, 2016-09-19, falls before that boundary and is therefore
excluded by the same rule that excludes everything else from those two
years. That is the cheapest of the three honest options. Correcting it
by hand means writing a per-symbol adjustment factor into the store
outside the connector, so the manifest hash stops describing what is on
disk — ADR-0063 rejected that arithmetic for the splits and it is no
better here. Dropping XLF loses the financials sector fund, the pair for
JPM, which is the one name with a positive result to explain. Starting
XLF's history at 2016-09-20 alone is a per-symbol carve-out that every
later reader would have to remember; a single study-wide start date needs
remembering once.

**Consequences.** Every run reads the same window, and it is written in
every run document. No symbol carries an uncorrected corporate action in
the readable range: the two stock splits are vendor-adjusted, XLE's and
XLK's 2025-12-05 2-for-1s are vendor-adjusted, and XLF's 2016 spin-off is
outside it. A run holds about 20% fewer bars. Existing fold dates and
counts are untouched. Should a later question genuinely need 2016-2017,
it is one number in one document — and it must then say what it does
about XLF, because this decision is the only thing keeping that row out.

---

## ADR-0067 — Skill is a pooled Diebold–Mariano gap, not a rejection count

**Status:** proposed (2026-09-03; plan `docs/plans/2026-09-horizon-search.md`
P5 — "what we count as success may pick the wrong look-ahead")

**Renumbered 2026-09-03 at integration.** This was written as ADR-0063 on
its own branch, at the same time as the price-scale decision that reached
`main` first under that number. The number here is the next free one, and
every reference in code, tests and result documents moved with it. The
branch also carried two copies of the heading, the first truncated
mid-sentence; they are collapsed to this one.

**Context.** Every horizon verdict so far has been a COUNT of Clark–West
rejections (`go_<sym>`, `h_star_<sym>`, `n_go`, `go_frac`). Clark–West is
a nested-model test: it adds back the variance the larger model pays for
estimating parameters, so it rejects on the POPULATION claim and can
reject while the forecast's realized MSPE is worse than the constant
mean's (Clark and West 2007's own simulations: 61% rejections at the 10%
level where the big model had the lower MSPE only 47% of the time).
`h01-gru` is the local instance — 12/60 rejections while forecasting 7.4%
worse than the mean. Meanwhile the number that DOES separate a forecast
from a correction, the MSPE gap, exists nowhere in the code: the "gain"
column in `docs/RE-ENTRY.md` was arithmetic done by hand in a research
doc, so the project's headline result is not reproducible by running
anything.

**Decision.** The verdict is the Diebold–Mariano test of the squared-error
loss differential against the fold's constant training mean, and it
REPLACES rejection-counting as the verdict.

1. Per fold `f`, series `s`, over the SAME validation rows the model is
   scored on: `d_t = (y_t - mu_f)^2 - (y_t - yhat_t)^2` with `mu_f` the
   fold's TRAIN mean of that series' label; `q_f = mean_t (y_t - mu_f)^2`.
   Positive `d` means the forecast beat the constant.
2. Pooled: concatenate `d_t / q_f` over the folds in TIME order (the
   `/q_f` makes disjoint folds scale-free, so a volatile fold cannot
   outvote a quiet one) and take a one-sided Newey–West `t` with Bartlett
   lag `max(h_steps - 1, floor(4 (n/100)^(2/9)))` and the
   Harvey–Leybourne–Newbold small-sample factor. `h_steps = lead /
   period_minutes`.
3. Across folds: `t_fold = mean_f(R2oos_f) / (sd_f / sqrt(F))` on `F - 1`
   degrees of freedom, where `R2oos_f = mean(d)/q_f`. This is the
   fold-cluster check the pooled HAC cannot make.
4. **PASS iff BOTH** `t_pool` clears the one-sided normal level AND
   `t_fold` clears the one-sided Student level (at F = 20 folds and
   alpha = 0.05 that is 1.645 and 1.729). One statistic alone passes on a
   single lucky fold, or on serial dependence the lag rule missed.
5. Group: average `d_t / q_f` ACROSS the series present at each timestamp
   first, then apply 2-4 to that one series (Qu–Timmermann–Zhu panel DM —
   the HAC on the cross-sectional average absorbs the dependence between
   names, so three names is not too few).
6. Report `R2oos_pool = 1 - sum(y - yhat)^2 / sum(y - mu_f)^2` over all
   scored rows beside every verdict: the pass is the sign of the win, the
   `R2oos` is its size.
7. Clark–West stays as a SIDE COLUMN (`cw_t`, `cw_reject_frac`), never
   the verdict. `go_frac` and `h_star` remain descriptive.

Because `d_t` is a within-fold quantity and every reported number is a
ratio inside one fold, the ADR-0059 label transforms do not disturb it.

**Consequences.** `NoInformationScan` must PERSIST `d_t` with its
timestamps — a fold's `mspe_model`/`mspe_mean` pair fixes `R2oos_f` and
therefore `t_fold`, but the pooled statistic and the group series cannot
be recovered from summaries, and neither can any later per-timestamp work
(P6's cross-sectional IC). The series is far past `carry.json`'s 20 kB
limit, so it goes through the node artifact seam
(`<fold>/artifacts/<node>/skill.json`, `runs.SKILL_FILE`) and
`runs.score_walk` reads the walk back.
**The 30 horizon-sweep walks already on disk saved no per-row
predictions**, so they can be re-scored on `R2oos` and `t_fold` only;
their pooled and group verdicts are unavailable and are reported as such
rather than guessed. The rule does NOT include a many-attempts
correction — that is P8, and it applies on top.

---

## ADR-0068 — Ordering and size are two measurements, and three names are not a cross-section

**Status:** proposed (2026-09-03; plan `docs/plans/2026-09-horizon-search.md`
P6 — "ordering and size may have different answers")

**Context.** The horizon sweep produced one finding nothing in the tree
measures: **rank information persists about thirty times further out
than forecast accuracy.** LightGBM's validation IC runs +0.054 at h=1
and is still +0.020 at h=60, positive in 19–20 folds of 20, while its
Clark–West gain over the same grid has fallen to −1.05%. If a
prediction-only model feeds an optimizer that SELECTS among names, that
gap is the whole question, and ADR-0067 scores only the size half of it.

Two defects sit under that.

**The number we call IC is not the number a selector needs.** `val_ic`
is a Spearman correlation over CONCATENATED `(name, time)` rows. It
pools the time-series dimension into the cross-sectional one: a model
that says only "all three names rise now" scores well pooled and gives
a selector choosing among names at ONE INSTANT nothing at all. That is
a plain Simpson's-paradox risk, and the practitioner rule is explicit —
correlate WITHIN each timestamp, then average over timestamps, never
pool asset-dates. So +0.054 is not evidence for the optimizer in either
direction.

**Nothing says whether the magnitude is recoverable.** For any forecast
with Pearson correlation r against the outcome, rescaling by
b* = cov(y, ŷ)/var(ŷ) gives R² = r² ≥ 0. A negative gain beside a
positive correlation is therefore a pure SCALING failure that one
number per horizon repairs. Whether the two-bar wall is an amplitude
artefact or an information wall is one regression away, and that
regression is not in the tree.

And a third thing, which is why this ADR carries a refusal rather than
only two functions: **with three names the per-instant rank correlation
is barely a statistic.** Enumerated, not estimated: it takes exactly
four values (−1, −0.5, +0.5, +1), it can never be zero, the six
orderings give a null standard deviation of 1/√2 = 0.707, and the best
one-sided p any single instant can reach is 1/6 = 0.167. Cross-sectional
demeaning leaves two degrees of freedom. It is a rescaled three-way hit
rate. At five names — which P9 restores — there are 120 orderings on a
0.1 grid, the null sd falls to 0.5, one instant can reach p = 0.008, and
the stamps needed to detect a true IC of 0.02 halve.

**Decision.** `dskit/pipeline/ordering.py` measures the two halves
apart, and refuses to present the second one when the panel is too thin
to carry it.

- **Size — `calibration_slope`.** Mincer–Zarnowitz: fit y = a + bŷ per
  fold, with a Newey–West standard error built from the OLS score
  series (`se(b) = n·se(u)/Sxx`, `u_t = (ŷ_t − ŷ̄)e_t`) at the same lag
  rule ADR-0067 uses, so both bands are built the same way. Report the
  slope against BOTH nulls: b = 0 (no linear information) and b = 1
  (the size is already right). `calibration_across_folds` pools the
  per-fold slopes with a Student t, because the folds are the
  independence units. A constant forecast is REFUSED, not scored as a
  slope of zero.
- **Order — `per_timestamp_ic`.** Rank the names within each instant by
  forecast and by outcome, correlate, and test the resulting time series
  with the existing HAC mean test. Its guard twin runs the same thing on
  series-demeaned pairs, which is what separates timing from a standing
  per-name tilt (a model tracking σ_t or an industry orders the names
  correctly every instant while timing nothing).
- **Both, named apart.** `pooled_name_time_ic` keeps the old pooled
  number under a name that says what it is, and `format_ordering`
  prints the two tables with the sentence "they are not the same
  number" between them. Neither can be read as the other by accident.
- **The refusal.** `USABLE_NAMES = 5`. Every result carries the names
  present at each instant (min, median, max) and the instants too thin
  to rank at all. Below the floor, `usable` is false with a reason
  attached, and `ordering_verdict` cannot pass, whatever the numbers
  say — a three-name ordering number must never look like evidence.
- **Reading a walk.** `runs.score_ordering` reads the ADR-0064 rows
  fold by fold and REDUCES each before the next, so a walk's rows are
  never all in memory. `python -m dskit.pipeline ordering <walk>`.

The pre-registered order rule: pooled per-timestamp t ≥ 1.645, more
than half the folds positive in mean rho, and the demeaned score
retaining at least half the raw one. PASS(magnitude) stays ADR-0067,
untouched.

**Consequences.** "How far ahead" is now allowed to be two numbers, and
which one applies depends on what the optimizer downstream does with a
forecast — selection at fixed size can live on order alone; anything
that SIZES a position cannot, because ranks have the same dispersion
every instant and destroy conviction. If the train-fitted slope lifts
the h = 3…60 gain above zero, the two-bar wall was amplitude, not
information. **On today's three names the cross-sectional verdict is
provisional by construction and the code says so on every row** — it
must be re-read after P9 restores AAPL and WMT. No node changed and no
walk was re-run: both measures are functions of rows already being
saved.

---

## ADR-0069 — The bar rises with the attempts behind it, and the shuffle unit is a whole day

**Status:** proposed (2026-09-03; plan `docs/plans/2026-09-horizon-search.md`
P8 — "many attempts need a fair bar")

**Context.** Thirty walks are already on disk, and the row-spacing,
price-definition, feature-block and model-setting searches multiply into
hundreds of scored cells on ONE dataset. With enough attempts the best
cell looks good whether or not anything is there, and a real horizon and
a lucky one score the same. ADR-0067 says how one cell is scored; it
says nothing about how high a cell must score once it was picked out of
hundreds.

Three things make the naive fixes wrong here. **Bonferroni and Holm
treat h = 2 and h = 3 as two attempts when they are nearly one** —
correct, and far too harsh. **White's Reality Check is the right shape**
— many forecasts, one benchmark, block-resampled — **but it is wrecked
by hopeless cells**, and we have many (the nets at −54%). And **plain
row shuffling is wrong twice over**: it destroys the minute-to-minute
autocorrelation AND it breaks the label overlap, since an h = 30 label
shares 29 minutes with the next one.

What finance says about the height: Harvey–Liu–Zhu put the honest hurdle
for a new claim at **t > 3.0**, not 2.0. The deflated-Sharpe expected
maximum of N pure-luck attempts is 2.73 at N = 180 — the CENTRE of the
luck distribution, so a floor and not a pass mark. Both transfer from a
Sharpe ratio to a squared-error test unchanged, because both are
statements about the maximum of many statistics that are N(0, 1) under
their own null, which ADR-0067's studentised statistic is.

**Decision.** `dskit/pipeline/attempts.py`, in three pieces, sitting ON
TOP of ADR-0067 and never replacing it.

- **A registry.** `AttemptRegistry` is an append-only ledger of every
  cell ever scored — model × horizon × row spacing × price field ×
  feature set × outcome unit — with the id derived from the knobs, so
  re-running a cell is not a new attempt and last week's attempt still
  costs its alpha. The count comes from the LEDGER, never from memory:
  a searcher asked to recall how many things they tried under-counts in
  the flattering direction every time.
- **The bar.** `max_bar` resamples every cell in one outcome unit's
  family JOINTLY: each cell's per-session sums are recentred (which
  imposes the null exactly), one ±1 coin is drawn per trading SESSION
  and SHARED by every cell and every name, and each cell's studentised
  statistic is recomputed. Sharing the coins is the whole mechanism —
  near-identical cells move together under the resample, so the
  procedure LEARNS from the data that they are almost one attempt. The
  pass mark is `max(c*, 3.0)` where c* is the 95th percentile of the
  best-of-all-cells statistic; Romano–Wolf stepdown gives the adjusted
  p-values; `implied_trials` reports what the grid was actually worth in
  independent tries, and Bonferroni and the deflated-Sharpe expectation
  ride beside it as reference. When the registry knows more cells than
  kept their rows, c* is declared a LOWER bound rather than quietly
  used as if it were the whole family.
- **The scramble.** The exchangeable unit is a **whole trading session,
  never a row**: a session is self-contained for every horizon tested
  here, so moving one moves every overlapping label with it, and nothing
  is reordered inside it. The cheap pass is the sign flip above (Shao's
  dependent wild bootstrap with session blocks), B = 10,000, arithmetic
  on stored numbers. The expensive pass — reshuffling which session
  donates the label and RE-RUNNING the walk about 100 times — is NOT
  built: `TIER2_SEAM` documents exactly where it plugs in, `tier2_plan`
  emits the permutations and `tier2_verdict` reads the finished runs,
  and the middle is deliberately absent because it is ~100 walks of
  compute and is for a WINNER only. Its second check is worth the six
  hours on its own: if the scrambled statistics do not sit near mean 0
  and sd 1, the variance estimator is wrong and every p-value in the
  project is wrong with it.

A cell passes only when ALL of: ADR-0067 passed **unchanged**; the
statistic clears the pass mark; the stepdown adjusted p clears 0.05;
and the WIN ITSELF is positive with its one-sided lower band above zero.
`python -m dskit.pipeline bar <walk>... --registry <ledger>`.

**Consequences.** The bar is stricter than anything applied so far and
it is meant to be: a grid this size has to clear about a t of 3 before
"we found the horizon" is a sentence anyone may write. Two things it
cannot do, stated so nobody assumes otherwise. **It cannot repair a cell
chosen after looking**, which is why the family count must come from the
ledger. And **the cheap scramble cannot test the fitting** — a label
that leaked into a feature is already baked into every stored forecast,
and only the tier-2 refit sees it. Memory is bounded by construction:
each walk is reduced to per-session sums and its rows dropped before the
next is read, so the bootstrap holds a few hundred numbers per cell
rather than a few hundred thousand, and the kept replicate matrix is
16 MB at 10,000 × 400. Pure noise was run through it — forty cells over
250 sessions, 10,000 replicates, every cell handed a PASSING skill
result so only this rule could refuse them — and nothing passed. **If
every cell fails the bar, that is the answer, not a reason to lower it.**
## ADR-0070 — Quotes arrive already reduced: one NBBO row per minute boundary

*(Written as ADR-0065 on `feat/quote-mid-pull`; renumbered to 0070 at the merge, where 0065 was already taken by the run-knobs decision. References to "ADR-0065" in the quote code and its docs mean this one.)*

**Status:** proposed (2026-09-03; P4's diagnostic left only the
quote-midpoint arm able to settle whether the H=1 gain is a price or a
print, and the tree holds no quote data at all)

**Context.** Every price in this study is the last trade of the minute.
A print sits at the bid or the ask, so that number flips by the spread
even when nothing changed. P4's diagnostic found the H=1 gain is entirely
LLY — 20 folds of 20, both models — while XOM loses; and LLY is the name
with the widest spread, the fewest prints per minute, and the only
negative one-minute autocorrelation in all eleven years, while XOM's
measures to zero. The ranking of the edge across names is exactly the
ranking of the flip. The same diagnostic killed the `vwap` arm: averaging
inside the bar manufactures a positive lag-one autocorrelation up to
Working's +0.25 ceiling, twenty times the effect under test. Only a
midpoint — which cannot bounce, because it is not a print — separates the
two hypotheses, and no field in the bar tree carries one.

The obvious shape, a quotes twin of `AlpacaBarsConnector` that stores what
the vendor returns, is not affordable and not wanted. Measured on this
cohort: 5.8 M NBBO updates on a 2022 session, 1.4 M on a 2026 one, about
3.1 M a day on average across five names. The free tier serves 200
requests a minute of 10,000 quotes each — 2 M quotes a minute, and
parallel workers only spend the same bucket faster (six of them earn a
429 in ten seconds). The full walk-forward era for five names is 2.7 B
quotes, roughly 23 hours of pulling and hundreds of gigabytes, to produce
1.7 M minute rows. The ratio is the finding: the asset is three orders of
magnitude smaller than the transport.

**Decision.** A second Alpaca pack, `dskit/onboarding/libs/alpaca_quotes.py`,
whose stream is `quote_minutes` and whose unit is the minute, not the
quote. Raw quotes are never stored: pages stream through a fixed-size
fold (`minute_rows`) and only the reduced row reaches disk.

- **The row is a boundary observation.** For bar minute `t`, the row is
  the LAST two-sided quote stamped in `[t, t+60s)` — the quote prevailing
  at the instant the minute's last trade also sits at, so `mid` and
  `close` are the same event seen two ways. Crossed (ask below bid),
  locked (ask equal to bid), one-sided and non-positive quotes are
  COUNTED but never selected, and `n_quotes`/`n_crossed`/`n_locked` ride
  along so market quality is measurable from the asset without going back
  to the vendor. `quote_age_ms` states how stale the chosen quote was at
  the boundary; `max_age_seconds` refuses one older than its own minute.
  A minute with no usable quote yields no row, so absence is absence, not
  a fabricated price.
- **`bid`, `ask` and `spread` are stored beside `mid`, not derived away.**
  The spread is a feature and a diagnostic in its own right (P3), and a
  mid without the half-width it came from cannot be audited.
- **Symbols are declared in PULL ORDER and each carries its own cursor.**
  The generic "skip anything at or before the cursor" rule assumes one
  time-ordered stream; here the pull is symbol-major, so the cursor is a
  map. It buys two things: the decisive names finish first and can be
  reported on while the rest are still coming, and a symbol added to the
  cohort later backfills from `start` while the others resume.
- **`budget_seconds` bounds one job.** A cursor advances only on a
  completed session, so a sixteen-month backfill is a sequence of
  resumable jobs that never half-writes a day.
- **Regular hours only, and `end` is the fetch bound** (ADR-0063's rule,
  applied here): out-of-hours quotes are most of the raw volume and no
  scored bar reads them, and a quote at or after the study's cut is never
  requested.
- **Transport is stdlib HTTP, not `alpaca-py`.** The SDK materializes a
  whole `QuoteSet` per request, which is the one thing this connector
  exists to avoid. Pacing is a client-side token bucket under the
  published limit; 429 and 5xx retry with exponential backoff.

**Consequences.** A midpoint price becomes selectable as `price_field`
without a new price tree: the rows key on `(symbol, ts)` like bars, land
in the same onboarding root under their own source name, and join on the
minute. The reduction is lossy AND irreversible — the intra-minute quote
path is gone, so anything wanting quote imbalance, effective spreads,
Lee–Ready signing or a sub-minute microprice must re-pull, and this pack
cannot serve it. That is the trade: a decade of raw NBBO for these names
is not storable here, and the question on the table is a minute-scale
one. Quotes are UNADJUSTED — the endpoint has no adjustment knob — so a
row is on the as-traded scale of its own day, matching `alpaca-sip` and
NOT `alpaca-sip-split`; joining mid to split-adjusted bars across a split
requires rescaling, which is why the first window was chosen to contain
none for this cohort. Cost is the binding constraint on coverage, not
disk: the first pull takes the last sixteen months to the cut, about five
and a half hours for five names, and extending it backwards is more
hours, not more code.

---

## ADR-0071 — Three switchable feature blocks, and the fold statistic that cannot peek

**Status:** proposed (2026-09-03; P2 and P3 of
`docs/plans/2026-09-horizon-search.md`. The P1 grid beats a flat average
guess at one and two minutes only, mostly on one name, and at nothing
beyond. The inputs are the leading suspect: only recent returns carry
history, the clock is crudely encoded, and there is no cross-stock or
market input at all.)

**Context.** `SessionFeatureRows` emits 86 columns. Twenty of them are
one-minute lags, forty are multi-scale return and volatility statistics
of the same series, thirteen are clock and calendar, two are SPY, five
are industry one-hots. Three research docs say what is missing:
`p2-time-of-day-feature-block-and-a-per-bucket-horizon-test.md`,
`p3a-bar-derived-inputs-that-may-extend-the-horizon.md` and
`p3b-cross-stock-market-and-sector-inputs-for-1-60-min-returns.md`.

Three problems had to be solved together. **Attribution:** adding
fifty-odd columns at once and re-running would say nothing about which
of them mattered. **Look-ahead:** three of the recommended inputs are
statistics of the sample — a per-minute volatility curve, a per-bucket
mean return, a volume norm by time of day — and a statistic fitted on
the whole sample is a leak whatever the rest of the pipeline does.
**Memory:** a twenty-fold walk already peaks near sixteen gigabytes of
seventeen usable, and a second job alongside one wedges the machine.

**Decision.**

- **Three named blocks, each switchable on its own.** `tod` (P2), `bar`
  (P3a) and `cross` (P3b), declared as
  `pipeline.features.params.feature_blocks`, a list. The default is the
  empty list: a document that names none gets byte-identical columns to
  what it got before, apart from the clock fix below. Block columns are
  appended AFTER every existing column and in a fixed order, so turning
  one on shifts nothing that was already there. `tod` and `bar` both
  want the two session-open returns; they are emitted once, so `bar`
  adds nine columns alone and seven beside `tod`.

  | block | columns | of which fitted per fold |
  |---|---|---|
  | `tod` | 31 | 2 |
  | `bar` | 10 | 1 |
  | `cross` | 17 | 0 |
  | all three | 56 | 3 |

- **The clock encoding was wrong and is fixed.** `tod_sin`/`tod_cos`
  used a whole circle over the session, so 09:30 and 16:00 landed on the
  same point — the one time of day where that is most wrong, given the
  open and the closing auction are the two least alike minutes of the
  day. The period is now half a circle: the open sits at angle zero, the
  close at pi, and every minute between has its own pair. This is a bug
  fix, not a knob, and it applies whether a block is on or not. **Runs
  recorded before 2026-09-03 carry the wrapped encoding in those two
  columns and are not comparable with later runs on them.**

- **Anything fitted is fitted per fold, in its own node.**
  `SessionFeatureRows` cannot see `$splits` — nothing upstream of it
  does, which is exactly why its build is shared by all twenty folds —
  so it emits every fitted column as a **zero placeholder**. A new kind,
  `intraday_equities-fold-stats`, is wired to
  `$splits.train_start_ms`/`$splits.train_end_ms`, runs once per fold,
  fits on rows inside that fold's training window alone and reads the
  result onto every row. It writes into the placeholder columns **in
  place**: they are the last columns of the frame, this node overwrites
  all of them every fold before anything reads them, and appending
  instead would copy the whole feature matrix once per fold to add three
  columns. It refuses to run unless the columns it is about to write are
  exactly the placeholders its declared blocks call for, so a mismatch
  between the two nodes is an error, not a silent column of zeros.

- **`cross` gets a feature-only symbol list.** The universe gains three
  optional fields: `cross` (symbols read for their returns and nothing
  else), `market` (the index proxy) and `sector_etf` (a map from each
  tradable to its fund). The mapping is **XLK/AAPL, XLF/JPM, XLV/LLY,
  XLP/WMT, XLE/XOM**, with SPY as the market — the same pairing the
  split-adjusted source was pulled for. `symbols` is now
  `tradable ∪ reference ∪ cross`. A `cross` symbol builds no feature
  frame and no tape.

- **A NaN inside every session is not acceptable.** `_frame_matrix`
  drops a scored row on any non-finite column, so a window that is NaN
  for the first k minutes of every session costs k/390 of every session
  forever. The volatility windows, the vol-scaled past returns and the
  accumulated residuals are therefore cross-session — a warmup once, not
  daily — exactly as the universe's own 60-minute scale already is. The
  lag columns are session-local, matching `ret_lag_*`, so they cost no
  row the baseline does not already lose. `ret_first30` reads zero
  before 10:00 rather than NaN, and `is_open30` is the flag that says
  the value is not yet formed.

**Consequences.**

*Attribution.* Each block can be measured alone against the same
baseline and the same folds, then in combination.
`configs/run-blocks-h01-ridge.json` is the template: it differs from
`run-multi3-h01-ridge.json` only in the universe file, the
`feature_blocks` list and the fold-stats node. The single question for
every column is ADR-0067's: does it extend the look-ahead at which we
beat a flat average guess. ADR-0069's bar applies to the family, and a
block that adds fifty columns has to clear it while paying for them.

*No look-ahead.* Two properties are asserted by test, and they are the
most important part of the change. For every bar-computable column,
**prefix invariance**: two tapes that agree up to bar t and differ
wildly after it must produce bit-identical columns at every bar up to t.
For every fitted column, **fold invariance**: scrambling the validation
rows must not move the fit, and a quiet training fold must not inherit a
loud validation fold's scale. One nuance is stated rather than hidden:
the per-minute volatility curve is smoothed across neighbouring clock
minutes, so a training row's own value can involve training rows at a
later clock minute of the same day. That is the standard
Andersen–Bollerslev seasonal shape and every row that enters it is
inside the training window; no validation row ever does.

*Memory.* One frame row costs 8 bytes per column, so a block costs
`columns × rows × 8` per scored symbol per fold, with the frame built
once and shared by every fold. On the shipped grid — five-minute rows
from 2018 to the cut, about 160 thousand rows per symbol, six frames —
that is roughly 240 MB for `tod`, 77 MB for `bar`, 131 MB for `cross`
and 430 MB for all three. Transient cost is one block's working arrays
at full one-minute resolution, about 60 MB, because every column is
reduced to the grid as soon as it exists rather than after all of them
are built. The reference return series held for the whole run are two
arrays per cross symbol, about 77 MB together. Against that, naming the
six funds in `cross` STOPS them building feature frames nobody scores —
they are in the split-adjusted store and no node filtered them out
before — which is worth roughly 660 MB. With
`configs/universe-blocks.json` the three blocks together are a net
reduction, not an increase.

*What is not here.* Trade count, the VWAP gap, the rolling
autocorrelation, average trade size, the Parkinson range, QQQ rotation
and peer residuals are all named in the research docs and all left out:
they rank below the ones built, and the first question is whether any of
this moves the horizon at all. QQQ is listed in `cross` only so it stops
building a frame; no column reads it.

---

## ADR-0072 — Equal tuning effort is part of a model's identity, and the zoo averages a declared seed set

**Status:** proposed (2026-09-03; P7's shortlist cannot be read as a
statement about model SIZE while the nets are the only arm that was
never searched)

**Context.** Five model classes have been run on this cohort and skill
fell as size grew: the three nets forecast 5% to 54% worse than a flat
average guess, with high in-sample ordering and negative out-of-sample
ordering. The P7 research doc
(`children/intraday_equities/docs/research/p7-model-size-for-this-noise-level-*.md`)
found that comparison is not a clean size comparison. Ridge carries a
6-draw search on a purged inner holdout — 63 days carved out of train
behind a 5-day embargo, chosen on IC, the fold's own validation never
read. LightGBM carries one in the multi3 wave. The `gru`, `lstm` and
`tft` documents carry **no tuning block at all**, `weight_decay: 0.0`,
a fixed 30 epochs with no stopping rule, and one seed. So part of
"bigger is worse" is a measurement of how much tuning each arm was
given, and it has been quoted as if it were a measurement of capacity.

Three of those four defects are config. `hpo_space` is a generic
discrete grid over `estimator_params`, so putting `epochs` in it **is**
an early-stopping rule chosen on data the fold's validation never sees,
and `weight_decay` **is** the L2 penalty — no new code for either. The
fourth is not expressible: `ZooEstimator` fits exactly one member from
`seed`, so every net number this study holds is a single draw from a
distribution whose spread was never measured, and the published habit
in this literature is to average ten seeds (five buys most of the gain).

**Decision.** Two parts.

*1. Tuning effort is declared, equal, and part of the cell's identity.*
Every cell on the P7 shortlist declares the SAME search as ridge: the
same purged inner holdout (63 days, 5-day embargo), the same `ic`
objective, the same `hpo_seed`, and six draws — five for PLS, whose grid
has exactly five points, and four for the seed-averaged cell, whose
every draw costs five fits. A model that cannot afford the search is
**not run** rather than run unsearched. A skill comparison across model
sizes in which only the small models were searched measures tuning
effort and must not be quoted as a size result.

*2. `ZooEstimator` gains one knob, `seeds`.* A non-empty list of whole
numbers fits one member per seed — the same architecture, the same
schedule, the same standardisation, differing only in initialisation and
batch shuffle — and `predict` returns the plain equal-weight mean of the
members. That is variance reduction with no added capacity: the
combination literature's case for the equal-weight average is that it
estimates no weights, which is the whole point when the signal is a
fraction of a percent. Omitted (the default, `None`) the estimator fits
the single member `seed` on exactly the old code path, bit-identical, so
no number recorded before this ADR moves. An empty list, a bare int, a
float or a bool is refused by name at construction: a mistyped seed set
is a typo, not an ensemble.

**Consequences.** The P7 shortlist ships as nineteen run documents,
`configs/run-p7-*.json`, each of which is its `run-p1-s05-h0N-ridge.json`
twin with the scan's estimator block swapped and **nothing else touched**
— same universe file and 5-minute row spacing, same split-adjusted store
from the 2018 study start, same RTH filter, same 20-lag/86-column
feature block, same five scored names, same 20 folds from 2022-05-06,
same vol-scaled SPY-residual label, same 30-minute scoring lattice, same
walk-forward objective, same tracking sink. A child test pins that, cell
by cell, against the twin.

Cost, measured at fold scale (196k training rows, 87 columns) on this
box, as model seconds per fold and the walk that implies on top of the
~3.5 minute pipeline overhead: PLS 2 s (~4 min), LightGBM held back
11 s (~7 min), `nlinear` 9 s (~6 min), the 4-unit GRU 11 s (~7 min, on
the GPU), the same GRU averaged over five seeds 28 s (~13 min), extra
trees 90 s (~33 min), and a random forest **543 s (~3 hours)** — the
bootstrap-and-best-split forest is about six times the extremely
randomized one at the same depth, so it is the one arm that should run
at H=1 only, and only if extra trees show something. Peak resident
memory added by the model itself is under 0.5 GB in every case, so none
of these changes the walk's existing ~11.5 GB profile; the two zoo cells
put their tensors in VRAM rather than in the 17 GB.

Nineteen more cells are nineteen more attempts under ADR-0069, which
raises the bar for everything already run. That is the declared price of
buying a fair reading of model size, and the shortlist should be run as
a family — H=1 first for all six model classes, then H=2 and H=3 only
for the classes that beat their P1 twin at H=1.

The `seeds` knob lives in `dskit`, not in the child, so a run of
`run-p7-gru4-seedavg-h01.json` from a worktree must put that worktree's
`dskit` ahead of the installed one:
`PYTHONPATH=~/wt/models python -m dskit.pipeline walkforward ... --adapter intraday_equities`
from the child root. Every other cell runs unchanged on either copy.

What this ADR does NOT do: it does not re-run the big nets. P7's hold
stands — a per-channel lag block (P3), the small net at least matching
ridge, seed averaging in place, and more names (P9) are the four things
that would have to be true first. This ADR supplies the third of them.

---

## ADR-0073 — A run reads only the bars it declared, and reads them once

*(Numbered 0073 because 0071 and 0072 were taken the same night on other
branches. Code and comments in this change say ADR-0073.)*

**Status:** proposed (2026-09-03; the P1 grid ran every look-ahead at
five-minute rows and could not run one-minute rows at all — two attempts
took the whole virtual machine down, so half of a search whose entire
point is that spacing and look-ahead must move together is blocked by a
memory defect rather than by a finding)

**Context.** A walk-forward holds the bar tape as Python dicts, and it
holds all of it. Measured on the split-adjusted store, one process, one
fold, the study window:

| what | records | cost |
|---|---|---|
| `scan_stream` returns the whole store | 15,991,833 | 11.9 GB resident, peaking at 12.3 GB |
| `start_ms` (ADR-0066) then drops 2016-2017 | 13,172,450 kept | the peak is already paid |
| six of the twelve names are never scored | 6,910,463 wanted | the other 6.3M are read, deduped, sorted and cached anyway |
| `BarsFromStore._scan` copies every record to add one field | — | +409 bytes each, 5.4 GB, live beside the originals |

Every one of those bounds is DECLARED before the read: the study's start
date is in the node's params, the cohort is in the universe file, and
regular hours are in the filter node wired to the bars node's output.
Each was applied to the returned list instead of to the read, which
costs the whole read plus a second list.

The result is a floor of about 11.5 GB before a single feature exists.
At five-minute rows the features add ~1 GB and a 20-fold walk fits, just,
on a 17 GB box. At one-minute rows they add five times that and the box
dies. The defect is not in the spacing; the spacing only exposes it.

Two smaller repetitions of the same shape sit downstream: the design
matrix is built three times inside the scan node (a finite-row select, a
lockbox-cut select, then a column-stack for the symbol code), and every
minute's ISO stamp is minted once per symbol where one canonical copy
would serve all twelve.

**Decision.** A run reads only the bars it declared, and reads them once.

1. `dskit.onboarding.scan_stream` gains three intake bounds — `since_ms`
   (inclusive lower bound on the derived epoch-ms field), `keep_values`
   (field to the values worth reading), and `admit` (a predicate for a
   bound no field carries, which may write the derived field it judged
   on). A record failing any of them is never allocated, never deduped,
   never sorted. Every line is still parsed and its key fields still
   checked, so a corrupt row inside a bound cannot hide behind one
   outside it. `ts_out` is therefore derived at INTAKE, and its three
   refusals now name the offending `path:line` instead of the dedup key.
2. `BarsFromStore` passes all three: `start_ms` as `since_ms`, the
   universe's `symbols` as the cohort bound, and a new optional
   `sessions` param as the session bound, whose predicate writes the
   `session` tag the node already owed. The tag is written INTO the
   scanned record; the node no longer copies the tape to add a field.
3. The scan node builds each name's design matrix in ONE allocation:
   `_frame_matrix` takes the lockbox cut and folds it into the
   finite-row mask, indexes rows and columns together, and accumulates
   the finite test column by column so the boolean matrix is never
   full-size. `_attach_symbol_codes` rewrites its list in place, so one
   name's pre-code matrix is doubled rather than all of them.

The cohort bound is unconditional, and it changes what the bars node
emits: `fingerprint()` counts fewer rows and hashes a different
snapshot, so a re-run of any existing document lands in a new run
directory. No NUMBER moves — the six dropped names are not tradable, not
the reference, carry no industry, and are cut by the `tradable` and
`names` filters before anything is fitted — but the identity does, and
the twenty-four walks already recorded cannot be reproduced
byte-identically under it. That is the price of the fix, stated here
rather than discovered later.

**Consequences.** Measured, same process, one fold at the last cutoff,
five names plus SPY, 87 feature columns, peak resident:

| case | before | after |
|---|---|---|
| one bounded year, one-minute rows | 12.25 GB | 2.48 GB |
| study window from 2018-01-01, five-minute rows | ~11.5 GB (the recorded walk figure) | 6.04 GB |
| study window from 2018-01-01, one-minute rows | did not fit; took the box down twice | 12.19 GB |
| from 2020-01-01, one-minute rows | — | 9.49 GB |

One-minute rows now fit. The 12.19 GB figure clears a 17 GB box with
headroom but not the 10 GB the fix was asked for, and the last row says
where the remainder is: `start_ms` is 2018-01-01, while the earliest bar
any of the twenty folds reads is 2020-05-07 (first validation 2022-05-06
less 730 training days), so two years and four months of tape is carried
by every fold and read by none. At five-minute rows that margin was
affordable; at one-minute rows it is about 2.7 GB. **A one-minute walk
should declare `start_ms` = 2020-01-01** — four months clear of the
earliest fold and of the longest feature warmup — and `sessions:
["rth"]`, which the session filter node then re-applies as a no-op. That
is a run-document choice, not a change to ADR-0066's study window: the
study still begins in 2018 for any document whose folds reach back that
far.

What remains after that is structural and is NOT proposed here. Of the
9.49 GB, about 3.2 GB is the bar tape as dicts — 900 bytes per record
for ten numbers — and it is dead weight from the moment the features
node has built its arrays, kept alive only because that node's output
contract is a list of dicts and the walk's caches are what stop a
105-second re-scan per fold. A columnar bar frame, the shape the
features node already emits as `tape`, would cost about 50 bytes per
record instead of 900. That is a change to the record contract of every
node between the store and the features, and it should be measured and
decided on its own.

---
## ADR-0074 — The expensive scramble is a run knob, and a session donates its whole label column

**Status:** proposed (2026-09-03; plan `docs/plans/2026-09-horizon-search.md`
P8 — "many attempts need a fair bar")

**Context.** ADR-0069 built both ends of a seam and left the middle out
on purpose. `tier2_plan` emits the day reshuffles, `tier2_verdict` reads
the finished runs, and `TIER2_SEAM` names the gap between them — the
part that actually re-runs the walk with the days shuffled, about a
hundred walks of compute, deliberately withheld "for a WINNER only".

There is now a winner. ADR-0069's cheap pass — the session sign-flip on
stored scores — left exactly two cells standing: LLY three minutes ahead
and the group two minutes ahead, both on one-minute rows with the tree
model. The cheap pass cannot finish the job, and ADR-0069 says why: **it
cannot test the fitting.** A label that leaked into a feature is already
baked into every stored forecast, so re-weighting those forecasts can
never reveal it. Only a refit can. The second question is worth the
compute on its own: if the scrambled statistics do not sit near mean 0
and sd 1, the variance estimator is wrong and every p-value in the
project is wrong with it — and nothing but a refit produces those
statistics.

**Decision.** The scramble is a **run knob**, not a script:
`label_scramble_seed` on the `intraday_equities-no-information-scan`
node, honoured by `_DayScramble` in the child's `nodes.py`. A scrambled
walk is therefore an ordinary walk-forward document, run by the ordinary
command, recorded in the ordinary run root and judged by the ordinary
ADR-0067 rule. Nothing about how it is scored is special, which is the
whole point: a null draw must travel the same path as the real result or
it is not that result's null.

Four things fix what the permutation is allowed to move.

- **The exchangeable unit is a whole trading session.** The label at
  (session i, minute m) becomes the label computed from session pi(i) at
  minute m. Sessions, never rows: a session is self-contained for every
  horizon tested here, so moving one moves every overlapping label with
  it and nothing is reordered inside it. PRESERVED: the within-session
  autocorrelation, the h-minute label overlap, the time-of-day shape,
  the day-level volatility clustering, and the cross-stock correlation
  at each minute. DESTROYED: only the link between the features at t and
  the return over [t, t+h].
- **One permutation for every symbol.** The donor map is drawn from a
  calendar read ONCE off the whole fold — the union over names, the
  largest row count any name has — so a name missing a session cannot
  shrink the pool or hand two names different maps. If the names did not
  move together the cross-stock correlation would be destroyed too, and
  the null would no longer be the null we mean.
- **Training and validation are drawn apart.** Each fold's training
  window and validation window get independent permutations, keyed by
  their own bounds. One shared shuffle would let a scrambled walk train
  on the sessions it is scored on.
- **The within-session key is milliseconds from that session's FIRST
  row, not the wall clock.** A summer session and a winter one then
  align despite the hour daylight saving moves the New York open in UTC.

Two refusals, both deliberate. A session shorter than 80% of the median
leaves the donor pool — permuting a half-day against a full one changes
the ROW COUNT rather than the labels. And a row whose donor session
lacks its minute is **refused (NaN) and dropped**, never given an
invented label. The cost is measured, not assumed: on the LLY cell a
scrambled walk scores 10,140 rows against the real walk's 10,266, which
is 1.2% — the boundary sessions of each window plus the ragged end of
the validation window. Filling those rows from their own real labels
would have kept the count exact by leaking real signal into the null,
which is the one thing this test exists to rule out.

The verdict is `tier2_verdict`, unchanged: the real walk must beat EVERY
scrambled one, and the scrambled statistics must sit near mean 0, sd 1.

**Consequences.** A run that declares `label_scramble_seed` says so in
its log and records the seed and the session count as metrics, because a
reader who mistakes one of these walks for a result reads a lucky draw
as an edge. B runs give a permutation p of at least 1/(B+1), so 19 runs
buy p = 0.05 and 99 buy p = 0.01; a family smaller than the planned 100
is honest only if the count is stated beside the answer. At about seven
and a half minutes and 13.7 GB a walk, one at a time, the full hundred
per cell is twelve and a half hours — which is why the count is reported
and not assumed.

One thing this ADR predicts and the runs must be read against. The
scrambled DM statistic is expected to sit **slightly below zero**, not
at zero: a fitted model with no signal is worse than the constant it is
measured against, because it adds estimation noise the constant does
not. That makes ADR-0067's threshold conservative rather than liberal.
The sd is the check that matters for the variance estimator; a mean that
drifts below zero is the fitting cost showing up, and a mean ABOVE zero
would be the alarming direction.

---

## ADR-0075 — A study resumes from journaled stages, and filtering never changes its fit

**Status:** accepted (2026-09-03; owner approved staged P10 execution,
study-wide correction, GROUP suppression and the memory strategy)

**Context.** P10 needs one JSON to coordinate a memory preflight, eight
horizon walks, two statistical gates, up to nineteen shuffled refits per
surviving horizon, and one final per-asset result. The existing seams are
close, but they do not compose that process.

- `PipelineDocument` owns one node DAG and an optional walk-forward. The
  `run`, `walkforward`, `skill` and `bar` verbs are separate invocations;
  `run_walk_forward` refuses an occupied summary directory and journals only
  after the whole walk finishes. There is no journal-backed stage resume.
- `score_walk` / `walk_cells` already recover every individual
  asset-horizon cell from streamed prediction files, and `max_bar` already
  shares one session coin across every cell passed to it. But `score_bar`
  partitions those cells by asset, so it cannot express one 200-cell study
  family. The readers also synthesize a `GROUP` row unconditionally.
- `NoInformationScan` already fits one pooled model and then scores each
  prepared asset. Its `records` input nevertheless decides BOTH which assets
  train and which assets are emitted, so filtering Gate-3 survivors upstream
  would silently shrink the fit.
- `BarsFromStore` can bound one source, `concat` can combine sources,
  `SessionFeatureRows(layout="columns")` already builds per-symbol arrays,
  and `PredictionWriter` streams scored rows. The remaining high-water marks
  are float64 feature/design arrays and pooled train/validation assembly.
- `dskit.journal` is an append-only, locked action ledger with readable
  execute rows. It is the correct checkpoint authority; the attempts JSONL
  remains the multiplicity ledger, not a second execution manifest.

**Decision.** Add generic staged orchestration to the node-map document,
then implement P10 as child-owned stage classes.

1. An optional top-level `stages` map is stored, emitted only when present,
   and included in document identity. A `Stage` abstract base, stage registry,
   default-deny `StageSpec`, planner and `staged` CLI verb mirror the node seam:
   each stage declares `uses`, stage-output `inputs`, and `params`; behaviour
   arrives by subclass or import path, never a `kind` branch. The planner
   validates the stage DAG, but the runner executes exactly one ready stage or
   child walk at a time. `run` and `walkforward` refuse a document carrying
   `stages`; derived child documents omit the section.
2. A completed stage appends one ordinary `execute` journal row containing a
   stable token derived from the parent document hash and stage key, the
   output path, state and output digest. Resume reads the journal first. An
   exact completed token with a present, digest-matching artifact is reused;
   a journal/artifact disagreement refuses; an artifact without the journal
   row is not completion. Child walks retain their existing journal rows and
   are reused by the same identity rule. Thus an interruption between walks
   resumes at the next walk. An interrupted walk remains a loud partial-run
   refusal rather than guessed completion. There is no independent stage
   completion manifest: stage JSON files are evidence artifacts, while the
   existing journal is the execution record. The memory child additionally
   persists its RSS beside its walk summary so a crash after that expensive
   walk can recover the measurement; the sidecar is accepted only with the
   child walk's journal evidence and is not stage completion.
3. P10's first computational stage is one isolated, most-recent 25-asset fold
   with the frozen one-minute geometry. It records peak RSS and must finish
   strictly below `17 * 1024**3` bytes before any full walk is eligible. A
   killed child or a larger peak halts the study. The permitted remediation,
   in measured order, is float32 feature/design matrices, single-allocation
   pooled training assembly, then bounded per-asset scoring after ONE pooled
   fit. Every option preserves all 25 training assets; batching may change
   what is resident, never what is fitted. The successful representation is
   then frozen into the study identity before Gate 1.
   The accepted representation transfers consumed source-list ownership, uses
   float32 OHLCV/working/feature arrays, atomically writes a SHA-256-manifested
   feature cache, verifies it once per staged invocation, and opens its arrays
   read-only by memory mapping in fold-isolated children. The v5 preflight
   measured 17,066,532,864 bytes (15.90 GiB), strictly below the
   18,253,611,008-byte limit, with manifest
   `0bd3d1a9c9c66328340396c8b05d0dc69ead9876096708ff5109731a888ff760`.
4. Gate 1 runs exactly eight separately fitted pooled models at horizons
   `[1, 2, 3, 5, 10, 20, 30, 60]`. Reduction disables synthetic aggregates,
   asserts exactly 25 named rows per horizon, and records all 200 cells in the
   existing attempts ledger before computing any survivor. For each asset,
   failure at 1 selects none; otherwise `gate1_h` is the furthest consecutive
   horizon whose pooled and across-fold ADR-0067 tests both pass. The first
   failed horizon is retained as evidence.
5. The reduction APIs gain injected aggregation and family strategies while
   keeping today's defaults byte-compatible. P10 supplies no aggregate
   strategy, so `GROUP` is never created, registered, corrected, shuffled or
   reported. It supplies one constant family strategy to `max_bar`, so Gate 2
   jointly resamples ALL 200 cells with the same session draws and performs
   one Romano–Wolf max-statistic correction. The selected `gate1_h` must clear
   the study pass mark, adjusted probability and positive lower bound. Failure
   selects none and never falls back to an earlier horizon.
6. `NoInformationScan` gains an optional score-only selection. It is applied
   after the 25 prepared series have built the pooled training matrix and the
   model has fitted; it controls only prediction/scoring emission. The scan
   records and asserts the training-series count separately from the scored
   count. Because SPY is both a fitted/scored asset and the residual reference,
   its own label uses its raw forward return with the same volatility scaling,
   rather than the identically-zero SPY-minus-SPY residual; the other 24
   labels retain SPY residualisation. This document-level policy prevents a
   nominal 25-asset fit from silently having only 24 finite label series.
   Gate 3 reads its score selections from Gate-2's saved artifact,
   groups survivors by their selected horizon, and for each unique horizon
   runs seeds 0 through 18 sequentially. Every seed refits the same frozen
   architecture on the same 25-asset universe; only survivor outputs are
   scored. With 19 null runs, the reported permutation probability is
   `(1 + nulls >= observed) / 20`, whose minimum is 0.05.
7. ADR-0074's null calibration becomes directional as its own consequence
   predicted: the spread must remain inside the frozen `[0.7, 1.4]` interval,
   while only a centre above `+0.3` is a calibration failure. A negative centre
   is estimation cost and conservative. Gate 3 still requires the real result
   to beat every shuffled refit.

The study document pins the 2026-02-28 cut and reads exactly the 25 assets in
the three split-adjusted sources: the original twelve, TSLA/TQQQ/NVDA/AMD,
and `alpaca-sip-split-c`'s UPRO, BAC, AMZN, AVGO, NFLX, MSFT, GOOGL, SMH and
IWM. META is absent and a runtime count/set assertion makes either drift a
refusal. The final artifact has one row per asset with `gate1_h`, all three
gate states, evidence counts, first failure and explicit `not_reached` reason.
Gate filters are written artifacts and are never hand-maintained symbol lists.

**Consequences.** Existing documents, hashes, `bar` output and `GROUP` rows do
not move because all new sections and strategies are opt-in. The new files are
`dskit/pipeline/stages.py`, `tests/pipeline/test_stages.py`,
`children/intraday_equities/intraday_equities/modelability.py`,
`children/intraday_equities/intraday_equities/feature_cache.py`,
`children/intraday_equities/tests/test_modelability.py`,
`children/intraday_equities/tests/test_feature_cache.py`, and
`children/intraday_equities/configs/run-p10-modelability.json`; package trees
and public exports are updated with them. Focused tests cover identity,
journal resume/refusal, the 200-cell registration barrier, a single correction
family, absence of `GROUP`, no Gate-2 fallback, immutable 25-asset fits,
seed coverage, cutoff/source pins and memory-gate ordering.
