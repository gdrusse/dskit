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
