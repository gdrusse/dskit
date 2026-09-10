# Gate 3 kickoff — cash flows and account state (mechanism only)

**Status:** START NOW, mechanism-only. **Model track:** OpenAI — GPT-5.6-Sol
writes the first TDD pass (used once); Terra reviewers/rewriters run every
skeptic round and every follow-up fix after that, one fresh agent at a time,
never in parallel.
**Suggested branch:** `codex/gate3-cashflows-<id>`, branched from
`origin/claude/phase1-recovery-seven-gates-ao4zdj` — **not** `main`. ADR-0114
and the completed Gate 1/Gate 2 work this plan builds on live only on that
branch; `main`'s decision-log tops out at the unrelated ADR-0112. Verify
ADR-0114 is actually present in `docs/architecture/decision-log.md` after
checkout before doing anything else.

Full scope: `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` §6
Phase 3 and §9 (reviewer lens 5, "Capital/accounting skeptic"); file
ownership: `docs/architecture/decision-log.md` ADR-0114, "Phase 3 — cash
flows and account state" section.

**Push every round.** Commit and `git push` after every commit — the first
pass, every skeptic fix, everything. Do not batch pushes to the end; nobody
can see unpushed work.

**Scope.** Add `dskit/production/cashflows.py` (new, tier 2, stdlib-only):
`RecurringCashFlowSchedule` plus immutable dated override/value objects
(skip/move/replace/withdraw/correct). Handle `zoneinfo`, recurrence
anchoring, half-open windows, DST, exact timestamp ordering, idempotent flow
IDs. It materializes due flow records; it never decides whether a real
transfer settled. Extend `dskit/production/{compose.py,state.py,report.py}`:
compose scheduled flows only for replay, keep production settlement-driven,
add generic TWR/MWR without changing the existing external-flow/PnL
separation.

**Blocked — do not do this part yet:** §11 item 2 (first biweekly-Friday
anchor date, holiday treatment, 09:30 same-instant ordering) blocks real
values in `configs/capital-policy.json` and the exact same-timestamp-ordering
test (plan §6 Phase 3 item 4). Build and test the schedule/override mechanism
against arbitrary/placeholder anchor dates and orderings; do not author
`capital-policy.json` with production values, and do not claim the
same-timestamp-ordering test is complete until item 2 is ruled.

**Process (mandatory, no exceptions):** TDD (failing test first), one
implementer + one fresh reviewer at a time — never parallel, never skip a
re-review after a fix. Write the phase ADR extending ADR-0114 and get it
accepted before implementation continues past config-shape tests. Follow root
`CLAUDE.md` OOP standards (subclass a hook, don't branch; one job per method;
`__all__`/`_` boundary; default-deny params; docstring conventions). Update
`dskit/production/{README.md,CLAUDE.md}` trees. Record actions through
`dskit.journal`; never hand-edit `docs/decisioning/path.csv`. Run only the
focused suites named in plan §10. Refresh `docs/RE-ENTRY.md` on wrap.
