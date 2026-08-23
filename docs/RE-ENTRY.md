# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 1617 pass, 82 skip

**Landed:** Package 2 **built**. Ratified ADR-0012…0015 + new ADR-0016
(P2 entity-free; entity association asserted P1-side) — OQ register is
clear. Then `dskit/onboarding` per the approved plan (`64865ed`):
Connector four-verb contract + protocol-1 envelope, WORM Merkle
snapshots under `raw/`, mode-keyed checkpoints, bitemporal normalized
rows (observations/forecasts split, declared not inferred), JSON
validation suites (dbt thresholds; block exits 3), certification gate
(block cannot certify), outbox publication keyed on the certification;
`dskit.assets` gained `sync-published` (ADR-0012 scan). 111 new tests:
purity gate (no `dskit.pipeline`), model pin `a8775903…` + parity with
`docs/architecture/onboarding-model.json`, localfiles conformance, CLI
e2e through sync. Package README/CLAUDE.md shipped; root CLAUDE.md and
architecture README updated to "built".

**Next:** nothing blocking. Declared seams when needed: tier-2 store
packs (ADR-0011), more connector packs in `onboarding/libs/`, semantic
validation above the engines. `ruff` unavailable in the anaconda env —
`pip install -e ".[dev]"` to lint.

**Decisions awaiting user:** none.
