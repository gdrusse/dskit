# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 1766 pass, 82 skip · **ruff:** clean (pinned)

**Landed this session:** parquet store pack (ADR-0019) — analytics-
scannable one-row-per-file backend, hardened through 7 adversarial
review rounds. Then the TODO register closed via ADR-0020 (integrity
parity): storage-key trust on vid AND kind axes across
file/sqlite/parquet, FileStore foreign-entry doctrine + wrapped I/O,
`\Z` anchors (incl. onboarding twin), sqlite URI `mode=rw`, purity-gate
level resolution + self-test, ruff baseline pinned. 3 review rounds,
clean pass, all mutants killed.

**Next session (user has the kickoff prompt):** orchestrator-mode
pass — remaining TODO minors, pmquant (`~/pmquant`) + rl_stocks
(GitHub, user grants access) capability-gap investigations, docs
overhaul (pipeline README missing, root README rebalanced to all
three pillars), child-module convention (`children/` incubation +
pinned skeleton, needs an ADR). No project-specific wrappers in dskit
— ever.

**Decisions awaiting user:** none.
