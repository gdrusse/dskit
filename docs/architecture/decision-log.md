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
  ts_out="asof_ms")` — one deduplicated snapshot of
  `observations/<source>/*/<stream>.jsonl[.gz]`: per declared
  `key_fields` tuple the row with the LATEST `acquired_at` wins
  (bitemporal supersede, ADR-0014's comparison convention via
  `parse_utc` when `ts_field` is declared). Codec-resolved per
  acquisition dir through `resolve_stream_file`/`iter_text_lines`
  (ADR-0036: loud on ambiguity, squats, and mid-stream corruption).
  **Memory discipline is the contract:** the returned records ARE the
  winning `data` dicts (the dedup dict is drained, never copied; the
  declared epoch-ms field is added in place) and every repeated
  string — JSON keys, key-field values, `acquired_at` — is collapsed
  to one canonical copy. Deterministic order: sorted by
  `(ts_out, *key_fields)` when `ts_field` is declared, else by
  `key_fields`. A missing `observations/<source>` directory refuses
  (default-deny: a typo'd root must not read as an empty store); an
  existing source with no stream files is truthfully empty. A row
  missing a key field, a non-dict `data`, or an unparseable
  `ts_field` refuses loudly, accumulated as `AssetError`.
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
`acquired_at` tie dedups quietly only when the data is IDENTICAL (the
at-least-once re-pull); differing data refuses — there is no
bitemporal winner, and this makes dedup content scan-order-independent
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
