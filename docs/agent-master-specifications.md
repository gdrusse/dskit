# AI Agent Master Specifications — Packages 1 & 2

> **Provenance.** Transcribed verbatim from 9 photographs (`IMG_0569`–`IMG_0577`,
> 2026-08-17) of two plain-text specification documents. Package 1 spans photos 1–5;
> Package 2 spans photos 6–9. Photos 4 and 5 are two shots of the *same* Package-1 tail
> (photo 5 is the complete one); other small line repeats at photo boundaries are scroll
> overlaps and have been de-duplicated. The source editor reported 3,762 characters for
> Package 1 and 3,334 for Package 2. The bodies below are reproduced exactly as written
> (in fenced blocks so nothing is reflowed).
>
> These are **design-discovery frameworks**, not implementation orders. Each document
> explicitly directs the agent to complete architecture before writing code.

---

## Package 1 — Enterprise Data Asset Platform

*(source tab: "Data Asset Platform Master Specification")*

```
AI AGENT MASTER SPECIFICATION
PACKAGE 1: ENTERPRISE DATA ASSET PLATFORM

ROLE OF THIS DOCUMENT
This document is both:
1. An architecture specification.
2. A design-discovery framework.

The agent must not immediately implement code.
The agent must first complete all architectural design work, close gaps,
challenge assumptions, maintain decision logs, and produce formal system
specifications.

MISSION
Create the authoritative system of record for analytical assets.

THE PLATFORM OWNS
 - Source Registry
 - Entity Registry
 - Dataset Registry
 - Dataset Version Registry
 - Data Product Registry
 - Feature Registry
 - Feature Version Registry
 - Feature Set Registry
 - Target Registry
 - Run Observation Registry
 - Artifact Registry
 - Output Registry
 - Lineage Registry

THE PLATFORM DOES NOT OWN
 - Data acquisition
 - Pipeline execution
 - Model training
 - Forecasting
 - Optimization
 - Scoring
 - Valuation execution
 - Scheduling
 - Orchestration

CORE PRINCIPLES
 - Asset-centric architecture
 - Entity-centric design
 - Full lineage
 - Immutable history
 - Version everything
 - Reuse before duplication
 - Observe execution rather than manage execution

MANDATORY DESIGN DELIVERABLES BEFORE IMPLEMENTATION
1. Domain Model Specification
2. Lifecycle Specification
3. Storage and Versioning Specification
4. Package Interaction Specification
5. API Specification
6. Security Specification
7. Testing Strategy
8. Deployment Strategy
9. Operational Monitoring Strategy
10. Disaster Recovery Strategy

DOMAIN MODEL SPECIFICATION REQUIREMENTS
Define:
 - Every domain object
 - Every field
 - Relationships
 - Cardinality
 - Ownership
 - State transitions

Objects must include:
 - Source
 - Entity
 - Dataset
 - DatasetVersion
 - DataProduct
 - Feature
 - FeatureVersion
 - FeatureSet
 - Target
 - RunObservation
 - Artifact
 - Output
 - LineageRelationship

LIFECYCLE SPECIFICATION REQUIREMENTS
Define lifecycle states for all major objects.

Example:
Draft -> Validated -> Certified -> Published -> Deprecated -> Retired

STORAGE AND VERSIONING SPECIFICATION REQUIREMENTS
Define:
 - PostgreSQL schema layout
 - Parquet storage layout
 - Partitioning strategy
 - Naming conventions
 - Snapshot strategy
 - Retention strategy
 - Archival strategy
 - Recovery strategy

PACKAGE INTERACTION SPECIFICATION
Define interactions with:
 - Data Acquisition and Onboarding Platform
 - Analytical Execution Framework

Document:
 - APIs
 - Events
 - Contracts
 - Ownership boundaries
 - Failure handling

FEATURE GOVERNANCE REQUIREMENTS
Features belong to entities.
Features never belong directly to analytical models.
Features are reusable platform assets.

TARGET GOVERNANCE REQUIREMENTS
Targets are independent assets.
Targets are stored independently from features.
Targets are joined to features only in run-specific artifacts.

DESIGN REVIEW QUESTIONS
For every component answer:
 - What problem does it solve?
 - Why does it belong in this package?
 - What are alternative designs?
 - What scalability limits exist?
 - What audit requirements exist?
 - What future requirements could invalidate the design?

DECISION LOG
Maintain formal architecture decision records.
No significant design decision may remain undocumented.

OPEN QUESTIONS REGISTER
Track unresolved questions.
Block implementation of high-risk components until resolved.

IMPLEMENTATION READINESS CHECKLIST
Before implementation:
[ ] Domain model approved
[ ] Lifecycle model approved
[ ] Storage design approved
[ ] APIs approved
[ ] Security approved
[ ] Versioning approved
[ ] Lineage approved
[ ] Testing strategy approved
[ ] Open issues addressed

AGENT OPERATING DIRECTIVE
Act as Lead Architect.
Challenge assumptions.
Identify weaknesses.
Propose alternatives.
Seek simplification.
Only authorize implementation after architecture stabilization.
```

---

## Package 2 — Data Acquisition and Onboarding Platform

*(source tab: "Data Onboarding Platform Master Specification")*

```
AI AGENT MASTER SPECIFICATION
PACKAGE 2: DATA ACQUISITION AND ONBOARDING PLATFORM

ROLE OF THIS DOCUMENT
This document is both:
1. An architecture specification.
2. A design-discovery framework.

The agent must complete architecture before implementation.

MISSION
Create the governed entry point through which all data enters the analytical ecosystem.

THE PLATFORM OWNS
 - Source Registration
 - Acquisition
 - Raw Snapshot Storage
 - Validation
 - Certification
 - Publication
 - Dataset Version Creation
 - Onboarding Lineage

THE PLATFORM DOES NOT OWN
 - Feature engineering business logic
 - Analytical execution
 - Training
 - Scoring
 - Optimization
 - Forecasting
 - Valuation logic

CORE PRINCIPLES
 - Snapshot everything
 - Preserve raw evidence
 - Validate before publish
 - Certify before registration
 - Support reproducibility
 - Separate acquisition from consumption

MANDATORY DESIGN DELIVERABLES BEFORE IMPLEMENTATION
1. Domain Model Specification
2. Lifecycle Specification
3. Storage and Versioning Specification
4. Package Interaction Specification
5. Connector Framework Specification
6. Validation Framework Specification
7. Security Specification
8. Testing Strategy
9. Deployment Strategy
10. Operational Monitoring Strategy

DOMAIN MODEL SPECIFICATION REQUIREMENTS
Define:
 - Source
 - SourceType
 - AcquisitionJob
 - Snapshot
 - ValidationResult
 - CertificationDecision
 - PublishedDataset
 - DatasetVersion
 - LineageRecord

LIFECYCLE SPECIFICATION REQUIREMENTS
Define lifecycle states for:
 - Sources
 - Snapshots
 - Certifications
 - Publications

CONNECTOR FRAMEWORK SPECIFICATION
Define architecture for:
 - API connectors
 - Database connectors
 - Hadoop connectors
 - File connectors
 - Vendor connectors

Must support future extension through plugins.

VALIDATION FRAMEWORK SPECIFICATION
Define:
 - Schema validation
 - Integrity validation
 - Completeness validation
 - Freshness validation
 - Custom validation rules

DATA MANAGEMENT RULES
Every acquisition must create an immutable snapshot.
Every snapshot must contain acquisition metadata.
Every certified dataset must be traceable to a source.

TIME SERIES RULES
Maintain:
 - effective date
 - acquisition date
Store forecasts independently from historical observations.

JOIN POLICY
Features and targets must not be joined here.
This package publishes governed datasets only.

PACKAGE INTERACTION SPECIFICATION
Define interactions with:
 - Data Asset Platform
 - Analytical Execution Framework

Document:
 - Publication events
 - Registration APIs
 - Failure handling
 - Ownership boundaries

DESIGN REVIEW QUESTIONS
For every component answer:
 - Why does this component exist?
 - What risks exist?
 - What alternatives exist?
 - What scaling challenges exist?
 - What audit requirements exist?

DECISION LOG
Maintain formal architecture decision records.

OPEN QUESTIONS REGISTER
Track unresolved design issues.

IMPLEMENTATION READINESS CHECKLIST
Before implementation:
[ ] Connector framework approved
[ ] Validation model approved
[ ] Storage model approved
[ ] Versioning model approved
[ ] Publication model approved
[ ] Security model approved
[ ] Monitoring strategy approved
[ ] Open issues addressed

AGENT OPERATING DIRECTIVE
Act as Lead Architect.
Drive requirements discovery.
Challenge assumptions.
Produce complete design specifications.
Only then authorize implementation agents.
```
