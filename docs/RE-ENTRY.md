# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `feat/assets-package` (merged to `main`) · **Tests:** 1506 pass, 82 skip

**Landed:** Package 1 **built** — `dskit/assets/` per ADR-0007…0010: model
grammar + default model (hash-pinned `176ed570`), content-hash records,
write-once `FileStore` behind the `Store` ABC, event-derived lifecycle,
DAG lineage, file-based `ingest-run` of real pipeline runs, full CLI.
78 new tests: purity gate, pipeline hash-parity, governance pin, e2e
ingest. Package `README`/`CLAUDE.md`, `examples/assets/custom-model.json`,
repo tree updated. Build loop used: brief → discuss → approve → write.

**Next:** Package 2 (Acquisition & Onboarding) design — its kinds arrive
as a model document (config, not code, per ADR-0007). Blocking open
questions: OQ-2 (P2→P1 handoff mechanism), OQ-4 (storage topology),
OQ-7 (entities). OQ-6 noted in the default model (forecasts land in
`output` until closed).

**Decisions awaiting user:** none — Package 1 scope closed this session.
