# Gate 4 kickoff — real forecast bundle and confirmed caps

**Status:** HOLD — hard-blocked. Do not assign until the owner rules §11
item 3 below. When started: **model track TBD** (unassigned).
**Suggested branch:** `<track>/gate4-forecast-bundle-<id>`.

Full scope: `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` §6
Phase 4 and §9 (reviewer lens 6, "MIO/guardrail skeptic"); file ownership:
`docs/architecture/decision-log.md` ADR-0114, "Phase 4 — real forecast bundle
and confirmed caps" section.

**Scope (once unblocked).** `children/intraday_equities/intraday_equities/
forecast_bundle.py` (new): point-in-time conversion from label units to gross
fractional returns, plus assembly/validation of the ADR-0088/MIO bundle.
Generic calibration estimators graduate to `dskit`; this file only binds
equity fields and reference policy. Extend `nodes_capital.py` to require the
pinned confirmed `(symbol, lead)` cap and refuse absent/zero/stale/
ineligible/over-cap rows, preserving the `stat_test` survivor wire.

**Why this is blocked, not just deferred:** §11 item 3 (the inverse-label
reference policy — how a predicted vol-scaled SPY-residual becomes a gross
return at each lead without future information) is the core of what
`forecast_bundle.py` does. There is no defensible mechanism to TDD against
until the owner rules it — starting this task before the ruling produces at
most an ADR and stops immediately. §11 item 4 separately means
`configs/run-mean-confirmation.json`'s data (March-May) is unreadable
regardless, even after item 3 is ruled.

**When the owner rules item 3, this kickoff's process is:** TDD (failing
test first), one implementer + one fresh reviewer at a time — never
parallel, never skip a re-review after a fix. Write the phase ADR extending
ADR-0114 and get it accepted before implementation continues. Follow root
`CLAUDE.md` OOP standards. Update the child's README/AGENTS/CLAUDE trees.
Record actions through `dskit.journal`. Run only the focused suites named in
plan §10.
