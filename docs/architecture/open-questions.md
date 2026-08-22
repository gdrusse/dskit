# Open Questions Register

Mandated by both master specs: *"Track unresolved questions. Block implementation of
high-risk components until resolved."*

**Risk** = how much downstream design depends on the answer. **Blocks** names the
deliverable(s) that cannot be frozen until this closes.

| ID | Question | Risk | Blocks |
|---|---|---|---|
| ~~OQ-1~~ | **Closed** → [ADR-0003](decision-log.md#adr-0003--package-2s-registration-of-a-source-is-operational-package-1-holds-the-authoritative-catalog): P1 catalog authoritative; P2 references it | — | — |
| ~~OQ-2~~ | **Closed** → [ADR-0012](decision-log.md#adr-0012--p2p1-handoff-is-a-pull-scan-the-published-store-is-the-outbox): pull scan over the published root; scan = delivery + anti-entropy | — | — |
| ~~OQ-3~~ | **Closed** → [ADR-0008](decision-log.md#adr-0008--pipelineassets-observation-is-file-based-no-imports-either-way): computed by the engine; registered via the file-based `ingest-run` seam | — | — |
| ~~OQ-4~~ | **Closed** → [ADR-0013](decision-log.md#adr-0013--p2-reuses-the-assets-engine-connectors-use-the-four-verb-contract): per-package roots at tier 1; shared DB deferred to store packs (ADR-0011) | — | — |
| ~~OQ-5~~ | **Closed** → [ADR-0009](decision-log.md#adr-0009--asset-version-identity-is-a-content-hash--human-alias): content hash + mandatory `name` alias | — | — |
| ~~OQ-6~~ | **Closed** → [ADR-0014](decision-log.md#adr-0014--bitemporal-storage-with-first-class-acquisition-modes): acquired forecasts in P2's `forecasts/` root; computed forecasts stay P1 outputs | — | — |
| ~~OQ-7~~ | **Closed** → [ADR-0016](decision-log.md#adr-0016--p2-is-entity-free-entity-association-is-asserted-p1-side): P2 entity-free; dataset_version→entity asserted P1-side after registration | — | — |

---

### OQ-1
**Who is the authoritative `Source` owner?** Package 2 owns *"Source Registration"*;
Package 1 owns a *"Source Registry"*. Options: (a) P1 is the catalog of record, P2
holds only operational config and references P1 IDs; (b) P2 registers and P1 mirrors
via events; (c) two distinct concepts that happen to share a name (a *connector
source* vs. a *catalog source*). **Leaning (a)** — it preserves "reuse before
duplication" and a single lineage root. Closing this ratifies
[ADR-0003](decision-log.md#adr-0003--package-2s-registration-of-a-source-is-operational-package-1-holds-the-authoritative-catalog).

### OQ-2
**P2→P1 handoff mechanism.** How does a published `DatasetVersion` reach the system
of record? Options: synchronous registration API call from P2; an event/outbox P1
consumes; or P1 polling P2. Must not lose the publication (certified data must
always become registered). Bears on failure handling, ordering, and idempotency in
the Package Interaction spec. Both specs mention *events* — an outbox/event on
publication is the leading option.

### OQ-3
**Where are Features and Targets computed, and who registers versions?** Package 1
owns the Feature/Target **registries** but "does not own feature engineering";
Package 2 explicitly excludes feature engineering; the **pipeline** is where
computation naturally happens. So a `FeatureVersion` is presumably *computed by the
engine* and *registered into P1* — but by what path, and is registration
synchronous with a run or a separate publish step? Interacts with the
"features belong to entities, reusable" governance rule and with
[ADR-0005](decision-log.md#adr-0005--package-1-observes-execution-it-never-triggers-it).

### OQ-4
**Storage topology.** Both specs mandate a PostgreSQL schema layout and a Parquet
layout with partitioning/retention/archival. Do the two packages (and the engine's
run outputs) share one physical store with per-package schemas, or does each own its
own? Snapshots (P2, raw evidence, heavy) and artifacts/outputs (P1) have different
retention profiles. Affects DR (P1 deliverable #10) and reproducibility.

### OQ-5
**Identity & versioning model.** `dskit.pipeline` already defines a strong identity
primitive: sha256 over canonical JSON, "same hash = same experiment." Should
`DatasetVersion` / `FeatureVersion` / `Target` versions reuse the same
content-addressed scheme (immutable, reproducible, dedupe-friendly), or use
monotonic version numbers, or both (a semantic version *plus* a content hash)?
Content-addressing aligns with "version everything" + "reuse before duplication",
but versions created in P2 must be stable through P1 registration
([ADR-0002](decision-log.md#adr-0002--datasetversion-is-created-in-package-2-registered-in-package-1)).

### OQ-6
**Forecast storage.** Package 2's time-series rules say *"store forecasts
independently from historical observations"* and to maintain effective vs.
acquisition dates. But forecasts are also plausibly pipeline **Outputs** owned by
Package 1. Where is the authoritative home for a forecast, and how do the two
date axes (effective, acquisition) travel with it? Low risk now; revisit at the
Lifecycle spec.

### OQ-7
**Entities.** Package 1 has an `Entity` registry and asserts *"features belong to
entities"*, but Package 2 has no entity concept — it publishes datasets. How is an
`Entity` defined, and how does an onboarded `DatasetVersion` get associated with the
entity/entities it describes? Is entity resolution a P1 responsibility applied after
registration, or declared during onboarding? Blocks the P1 domain model's core
relationships.

---

## How to use this register

- A question is **closed** by ratifying (or adding) an ADR in
  [decision-log.md](decision-log.md) and striking the row here with a link.
- 🔴 high-risk questions **block** their listed deliverable — do not freeze that
  deliverable's design (and certainly no code) until they close.
- New questions surfaced during any deliverable are appended here, not buried in
  prose.
