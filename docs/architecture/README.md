# dskit — Architecture & Design (Packages 1 & 2)

This directory holds the **architecture-first** design work mandated by
[`../agent-master-specifications.md`](../agent-master-specifications.md). Per both
master specs, **no implementation code is written until the architecture
stabilizes** — the design deliverables below come first.

> **Operating posture (verbatim from the specs).** *Act as Lead Architect.
> Challenge assumptions. Identify weaknesses. Propose alternatives. Seek
> simplification. Only authorize implementation after architecture stabilization.*

## The ecosystem — three packages

The two master specs describe two **new** platforms. Both explicitly name an
**"Analytical Execution Framework"** as a separate collaborator that neither of
them owns — and that framework **already exists**: it is the `dskit.pipeline`
engine. So `dskit` is being built as a three-package ecosystem:

| Package | Role | Home (proposed) | Status |
|---|---|---|---|
| **Analytical Execution Framework** | runs a declared pipeline (data → predict → optimize → report); emits run records | `dskit/pipeline/` | ✅ built |
| **Package 1 — Data Asset Platform** | authoritative **system of record**: registries + lineage; immutable, versioned; **observes** execution, never manages it | `dskit/assets/` | ✅ built |
| **Package 2 — Data Acquisition & Onboarding** | governed **entry point**: acquire → snapshot → validate → certify → publish | `dskit/onboarding/` | ✅ built |

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
  *No significant design decision may remain undocumented.*
- **[open-questions.md](open-questions.md)** — unresolved questions. *High-risk
  items block implementation of the components they touch.*

## Progress

| Artifact | State |
|---|---|
| Context & ownership map (deliverable #4 foundation) | ✅ drafted |
| Decision Log | ✅ ADR-0001…0016 (0012…0016 ratified 2026-08-22) |
| Open Questions Register | ✅ clear — OQ-1…OQ-7 all closed |
| Package 1 design + build | ✅ built (`dskit/assets/`, ADR-0007…0011) |
| Package 2 design + build | ✅ built (`dskit/onboarding/`, ADR-0012…0016; design: [onboarding-design.md](onboarding-design.md)) |

**Current work:** both packages built and tested. Remaining seams are
declared, not urgent: tier-2 store packs (ADR-0011), connector packs
beyond `localfiles`, semantic validation above the engines.

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
