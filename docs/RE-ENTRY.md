# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `docs/package-specs` · **Tests:** 860 pass, 22 skip (tier-2 libs not installed)

**Landed:** repo-level `CLAUDE.md` (standards + working agreement). Architecture
foundation in `docs/architecture/`: context/ownership map, 6 ADRs, 7 open questions.
`dskit.pipeline` = the specs' "Analytical Execution Framework" (ADR-0001).

**Next:** Deliverable #1, Domain Model — recommend a shared core first
(Source/DatasetVersion/Lineage are common to Packages 1 & 2).

**Blocked on you** (see `docs/architecture/open-questions.md`):
- OQ-1 — authoritative `Source` owner: P1 catalog, P2, or synced?
- OQ-3 — who registers a `FeatureVersion`, and when relative to a run?
- OQ-5 — asset identity: reuse pipeline's sha256 content hash, version numbers, or both?
