# dskit — Architecture & Design (Packages 1 & 2)

This directory holds the **architecture-first** design work mandated by
[`../agent-master-specifications.md`](../agent-master-specifications.md). Per both
master specs, **no implementation code is written until the architecture
stabilizes** — the design deliverables below come first.

> **Operating posture (verbatim from the specs).** *Act as Lead Architect.
> Challenge assumptions. Identify weaknesses. Propose alternatives. Seek
> simplification. Only authorize implementation after architecture stabilization.*

## The ecosystem — four packages

The two master specs describe two **new** platforms. Both explicitly name an
**"Analytical Execution Framework"** as a separate collaborator that neither of
them owns — and that framework **already exists**: it is the `dskit.pipeline`
engine. So `dskit` is four packages:

| Package | Role | Home (proposed) | Status |
|---|---|---|---|
| **Analytical Execution Framework** | runs a declared pipeline (data → predict → optimize → report); emits run records | `dskit/pipeline/` | ✅ built |
| **Package 1 — Data Asset Platform** | authoritative **system of record**: registries + lineage; immutable, versioned; **observes** execution, never manages it | `dskit/assets/` | ✅ built |
| **Package 2 — Data Acquisition & Onboarding** | governed **entry point**: acquire → snapshot → validate → certify → publish | `dskit/onboarding/` | ✅ built |
| **Child action journal** | per-child ledger of acquire / research / execute / production; CSV store; generated markdown; owner path-to-production | `dskit/journal/` | ✅ built (ADR-0056) |
| **Production layer** | serves an immutable release forward on a cadence: guards, executor seam, authenticated arming, hash-chained ledger, monitors, alerts, health | `dskit/production/` | ✅ built (ADR-0090, ADR-0091) |

Package homes are proposals — see [ADR-0006](decision-log.md#adr-0006--package-homes-and-names).
The full ownership map and end-to-end data flow are in
**[context-and-ownership.md](context-and-ownership.md)**.

## Mandated design deliverables

Each spec lists ten deliverables. They agree on #1–#4, then diverge:

| # | Package 1 (Data Asset Platform) | Package 2 (Acquisition & Onboarding) |
|---|---|---|
| 1 | Domain Model Specification | Domain Model Specification |
| 2 | Lifecycle Specification | Lifecycle Specification |
| 3 | Storage & Versioning Specification | Storage & Versioning Specification |
| 4 | Package Interaction Specification | Package Interaction Specification |
| 5 | API Specification | Connector Framework Specification |
| 6 | Security Specification | Validation Framework Specification |
| 7 | Testing Strategy | Security Specification |
| 8 | Deployment Strategy | Testing Strategy |
| 9 | Operational Monitoring Strategy | Deployment Strategy |
| 10 | Disaster Recovery Strategy | Operational Monitoring Strategy |

**Cross-cutting, maintained continuously** (mandated by both specs):

- **[decision-log.md](decision-log.md)** — formal architecture decision records.
- **[../adhoc/deferred_decisions.md](../adhoc/deferred_decisions.md)** — parked locks, not ADRs.
- **[../decisioning/README.md](../decisioning/README.md)** — child evidence grids; no science call without a linked run.
  *No significant design decision may remain undocumented.*
- **[open-questions.md](open-questions.md)** — unresolved questions. *High-risk
  items block implementation of the components they touch.*

## Progress

| Artifact | State |
|---|---|
| Context & ownership map (deliverable #4 foundation) | ✅ drafted |
| Decision Log | ✅ ADR-0001…0031 (0021…0026 + 0031 accepted 2026-08-25, owner-ratified; 0027…0030 accepted by owner directive) |
| Open Questions Register | ✅ clear — OQ-1…OQ-7 all closed |
| Package 1 design + build | ✅ built (`dskit/assets/`, ADR-0007…0011; store packs sqlite ADR-0018 + parquet ADR-0019; integrity parity ADR-0020) |
| Package 2 design + build | ✅ built (`dskit/onboarding/`, ADR-0012…0016; `restapi` pack ADR-0017; design: [onboarding-design.md](onboarding-design.md)) |
| Child convention | ✅ `children/` incubation + pinned skeleton (ADR-0021) |
| Engine parity with the parent fork | **complete**: ADR-0022/0023 (flow + table kinds), ADR-0024 (split policies + event bounds), ADR-0025 (declared-model seam + trainlog + curve streaming), ADR-0026 (full report renderers) — see [child-gap-pmquant.md](child-gap-pmquant.md); ADR-0031 extends walk-forward folds with split policies |

**Current work:** all three pillars built and tested. Capability-gap
reports for the first two child candidates:
[pmquant](child-gap-pmquant.md), [rl_stocks](child-gap-rl-stocks.md).

## Implementation readiness checklist (from the specs)

Implementation of a component is **blocked** until its rows are approved. Tracked
here as a reminder that green code is not the bar — an approved design is.

- [ ] Domain model approved
- [ ] Lifecycle model approved
- [ ] Storage & versioning approved
- [ ] APIs / connector framework approved
- [ ] Validation / security approved
- [ ] Lineage approved
- [ ] Testing strategy approved
- [ ] Open issues addressed
