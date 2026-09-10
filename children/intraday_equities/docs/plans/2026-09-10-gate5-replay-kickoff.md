# Gate 5 kickoff — stateful replay through the production seams

**Status:** START NOW — §11 items 5 and 6 are ruled (2026-09-10, below).
Gate 5a (the conformance-discovery sub-task) is already done and
skeptic-clean on `cursor/gate5a-replay-conformance-1656`; that branch's
evidence is the starting point here, not a restart.
**Model track:** Grok Cursor 4.6 writes the first TDD pass (used once) —
chosen because Cursor/Grok already produced the clean Gate 5a conformance
result this task builds on, on this exact surface. After that, fresh
reviewer/rewriter agents run every skeptic round and every follow-up fix,
one at a time, never in parallel.
**Suggested branch:** `cursor/gate5-replay-<id>`, branched from
`origin/claude/phase1-recovery-seven-gates-ao4zdj` — **not** `main`. ADR-0114
and the completed Gate 1/Gate 2 work this plan builds on live only on that
branch; `main`'s decision-log tops out at the unrelated ADR-0112. Verify
ADR-0114 is actually present in `docs/architecture/decision-log.md` after
checkout before doing anything else.

**Push every round.** Commit and `git push` after every commit — the first
pass, every skeptic fix, everything. Do not batch pushes to the end; nobody
can see unpushed work.

Full scope: `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` §6
Phase 5 and §9 (reviewer lens 7, "Replay/production-parity skeptic"); file
ownership: `docs/architecture/decision-log.md` ADR-0114, "Phase 5 — stateful
replay through the production seams" section. Gate 5a evidence: `docs/memos/
2026-09-09-final-model-replay-gate5a-skeptic-conformance*.md` and
`-architecture.md` on `cursor/gate5a-replay-conformance-1656` (both skeptics
PASS, 0 Major — journal action A18857). Read those memos before writing any
code; they already found the smallest missing hook (if any) in
`ServeLoop`/`ReplayFeed` — do not re-derive that finding from scratch.

## §11 item 5 — RULED (2026-09-10, owner)

**Mixed confirmed caps across all horizons** (not h1-only). This is the
plan's harder alternative — build for the full multi-horizon confirmed-cap
ladder, not a single-horizon simplification. Note the dependency this
creates: REAL confirmed (deployment-eligible) caps don't exist yet — Gate 4's
cap confirmation is itself blocked on §11 item 4 until untouched March-May
data is available. So build and test the general multi-horizon,
overlapping-decision replay MECHANISM now, exercised only against
development-only/synthetic caps via `configs/run-development-replay.json`
(forced `deployment_eligible=false`) — never claim a real mixed-cap replay
result, per plan §8.

The owner ruled the SHAPE, not every mechanic: exact overlap semantics
(e.g. whether two different-horizon decisions on the same name may be open
concurrently, whether a new signal can override an earlier still-open one,
how a same-tick collision between an h1 exit and an h10 hold resolves) are
not decided here. Propose a concrete rule in your phase ADR, grounded in the
plan's one-decision-graph architecture (§3) — do not silently invent it in
code without it being in the ADR for acceptance.

## §11 item 6 — RULED (2026-09-10, owner)

**Next-bar-open market fill**, as a config-driven fill-policy, not hardcoded:
decide at bar close `t` using only data known by `t`; fill at bar `t+1`'s
open. Reuse the existing Schwab cost model already in `nodes_capital.py` for
spread/fees. No partial fills or rejections in replay (full simulated fill
at that price); a halted symbol is skipped, not queued. Forced exit at
horizon expiry (`t+h`) using that bar's open. **Every one of these values
(fill bar offset, cost-model reference, partial-fill/rejection behavior,
halt handling, forced-exit timing) must be a named field in a config
document** (extend `configs/capital-policy.json` or add a sibling
fill-policy config — your ADR picks which, per existing file-ownership
conventions), pinned by digest like every other dskit config, never a
Python literal a future change requires editing code to reach.

## Scope

`children/intraday_equities/intraday_equities/replay.py` (new): thin equity
policies for bar choice, execution timing, forced exits, horizon identity,
and the Schwab cost/fill adapter, driven by the config above — subclass or
compose `dskit.production`, never own clocks/ledgers/account folds/generic
performance math. Extend `dskit.production.loop.ServeLoop`/`ReplayFeed`/
clock/executor only via the exact hook Gate 5a's conformance test
identified — do not invent a new one without re-justifying it. Add
`configs/run-development-replay.json` (replay already-spent P16 evidence
only, force `deployment_eligible=false`).

The Schwab adapter's mechanism may be built and synthetically tested even
though real broker/tax rulings (§11 item 9) remain open — per the plan's own
§2 ruling, that forbids premature empirical runs, not tests. Do not pin real
broker parameters.

**Process (mandatory, no exceptions):** TDD (failing test first, starting
from Gate 5a's conformance finding), one implementer + one fresh reviewer at
a time — never parallel, never skip a re-review after a fix. Write the phase
ADR extending ADR-0114 (carrying the §11 items 5/6 rulings above and your
proposed overlap-semantics rule) and get it accepted before implementation
continues past config-shape tests. Follow root `CLAUDE.md` OOP standards.
Update the child's README/AGENTS/CLAUDE trees and
`dskit/production/{README.md,CLAUDE.md}` if a generic hook is touched.
Record actions through `dskit.journal`; never hand-edit
`docs/decisioning/path.csv`. Run only the focused suites named in plan §10.
Refresh `docs/RE-ENTRY.md` on wrap.
