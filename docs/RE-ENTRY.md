# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 1766 pass, 82 skip · **ruff:** clean (pinned)

**Landed this session:** parquet store pack (ADR-0019) — analytics-
scannable one-row-per-file backend, hardened through 7 adversarial
review rounds. Then the full TODO register closed via ADR-0020
(integrity parity): storage-key trust on vid AND kind axes across
file/sqlite/parquet, FileStore foreign-entry doctrine + wrapped I/O,
`\Z` anchors (incl. onboarding twin), sqlite URI `mode=rw`, purity-gate
level resolution + self-test, ruff baseline pinned. 3 review rounds,
clean pass, all mutants killed.

**In progress (overnight, this session):** orchestrated pass — pmquant
capability-gap investigation, docs overhaul (pipeline README, root
README rebalance, child-module convention: `children/` incubation +
skeleton, ADR-0021), small generic gaps if found. rl_stocks deferred
(GitHub access next session).

**Next:** review overnight commits; grant rl_stocks GitHub access and
run its investigation; then first child module built to the new
convention.

**Decisions awaiting user:** none (overnight work lands as commits for
morning review).
