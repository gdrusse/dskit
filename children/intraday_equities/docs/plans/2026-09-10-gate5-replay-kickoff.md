# Gate 5 kickoff — stateful replay through the production seams

**Status:** HOLD for the policy work — hard-blocked. Gate 5a (the
conformance-discovery sub-task) is already done and skeptic-clean on
`cursor/gate5a-replay-conformance-1656`; that branch's evidence is the
starting point here, not a restart. **Model track TBD** (unassigned) for the
remainder.
**Suggested branch:** `<track>/gate5-replay-<id>`.

Full scope: `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` §6
Phase 5 and §9 (reviewer lens 7, "Replay/production-parity skeptic"); file
ownership: `docs/architecture/decision-log.md` ADR-0114, "Phase 5 — stateful
replay through the production seams" section. Gate 5a evidence: `docs/memos/
2026-09-09-final-model-replay-gate5a-skeptic-conformance*.md` and
`-architecture.md` on `cursor/gate5a-replay-conformance-1656` (both skeptics
PASS, 0 Major — journal action A18857).

**Scope (once unblocked).** `children/intraday_equities/intraday_equities/
replay.py` (new): thin equity policies for bar choice, execution timing,
forced exits, horizon identity, and the Schwab cost/fill adapter — subclass
or compose `dskit.production`, never own clocks/ledgers/account folds/generic
performance math. Extend `dskit.production.loop.ServeLoop`/`ReplayFeed`/
clock/executor only via the smallest generic hook Gate 5a's conformance test
already showed is missing (do not invent a new hook without re-deriving that
finding). Add `configs/run-development-replay.json` (replay already-spent
P16 evidence only, force `deployment_eligible=false`).

**Why this is blocked, not just deferred:** §11 item 5 (first replay horizon
policy — h1-only vs. mixed confirmed caps; exit/expiry/overlap semantics) and
item 6 (fill model — order type, decision/fill bar, latency, partials,
rejections, spread/slippage, halts, mark source) both block `replay.py`'s
core policies outright. The Schwab adapter's *mechanism* may be built and
synthetically tested without real broker/tax rulings (§11 item 9) per the
plan's own §2 ruling, but its policy parameters cannot be pinned yet.

**When the owner rules items 5 and 6, this kickoff's process is:** TDD
(failing test first, starting from Gate 5a's conformance finding), one
implementer + one fresh reviewer at a time — never parallel, never skip a
re-review after a fix. Write the phase ADR extending ADR-0114 and get it
accepted before implementation continues. Follow root `CLAUDE.md` OOP
standards. Update the child's README/AGENTS/CLAUDE trees and
`dskit/production/{README.md,CLAUDE.md}` if a generic hook is added. Record
actions through `dskit.journal`. Run only the focused suites named in plan
§10.
