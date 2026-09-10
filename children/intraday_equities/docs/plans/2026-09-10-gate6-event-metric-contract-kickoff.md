# Gate 6 kickoff — shared event and metric contract

**Status:** START NOW. **Model track:** Claude — Opus writes the first TDD
pass (one sub-agent, used once); Sonnet runs every skeptic round and every
follow-up fix, one fresh agent at a time, never in parallel.
**Suggested branch:** `claude/gate6-event-metric-contract-<id>`, branched from
`origin/claude/phase1-recovery-seven-gates-ao4zdj` — **not** `main`. ADR-0114
and the completed Gate 1/Gate 2 work this plan builds on live only on that
branch; `main`'s decision-log tops out at the unrelated ADR-0112. Verify
ADR-0114 is actually present in `docs/architecture/decision-log.md` after
checkout before doing anything else.

Full scope: `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` §6
Phase 6 and §9 (reviewer lens 8, "Observability skeptic"); file ownership and
the verbatim event-schema category list: `docs/architecture/decision-log.md`
ADR-0114, "Phase 6 — shared event and metric contract" section.

**Push every round.** Commit and `git push` after every commit — the first
pass, every skeptic fix, everything. Do not batch pushes to the end; nobody
can see unpushed work.

**Scope.** Add only generic, low-cardinality operational readings/reducers to
`dskit/production/{metrics.py,monitors.py,vocab.py}` that are genuinely
absent, plus `children/intraday_equities/intraday_equities/metrics.py` (new)
for equity event-field adapters and domain metrics (e.g. signal decay by
lead). Define the versioned event schema itself (identity/invariants, data,
model, gates, MIO, execution, portfolio/capital, operations/parity — exact
categories in ADR-0114/plan §6). Record every metric value now; do not add
WARN/HOLD alert thresholds. Symbol/lead never enter closed telemetry label
sets unless cardinality is explicitly bounded and approved — keep full detail
in ledger/report artifacts, exporters publish safe aggregates only.

**Not blocked:** recording the metric values is unblocked. **Blocked:** §11
item 7 (WARN/HOLD thresholds) and item 10 (alert/reporting requirements
beyond the catalogue) — stop at "record only" for those, do not invent
thresholds.

**Process (mandatory, no exceptions):** TDD (failing test first), one
implementer + one fresh skeptic at a time — never parallel skeptics, never
skip a re-review after a fix. Write the phase ADR extending ADR-0114 and get
it accepted before implementation continues past config-shape tests. Follow
root `CLAUDE.md` OOP standards (subclass a hook, don't branch; one job per
method; `__all__`/`_` boundary; default-deny params; docstring conventions).
Update `dskit/production/{README.md,CLAUDE.md}` and the child's
README/AGENTS/CLAUDE trees. Record actions through `dskit.journal`; never
hand-edit `docs/decisioning/path.csv`. Run only the focused suites named in
plan §10. Refresh `docs/RE-ENTRY.md` on wrap.
