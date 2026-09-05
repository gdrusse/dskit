# `dskit.production` build — status

Updated every commit. **Complete** = code green + committed. **Remaining** = not started or in flight.

## COMPLETE (tests green, committed)

| group | modules | tests |
|---|---|---|
| G1 | `vocab.py` `base.py` `redact.py` `records.py` `__init__.py` | 510 + review's 62 |
| G2 | `document.py` `release.py` | 307 |
| G3 | `clock.py` `sessions.py` `cadence.py` | 113 |
| G4 | `ledger.py` (+ `validate_cache_head`, `HeadBoundCache`) | 154 |
| G5 | `state.py` (SeriesState fold, StateView, TickState, PositionBook, Recovery) | 108 |
| G6 | `resilience.py` `metrics.py` | 396 |
| G7 | `control.py` `accounting.py` | 256 |
| G8 | `guards.py` | 352 |
| G9 | `breaker.py` `arming.py` `coordination.py` | 323 |
| G10 | `ids.py` `bundles.py` `policy.py` (+ 17,280-cell golden table) | 334 |
| G11 | ADR-0091 pipeline seam (`SubgraphRunner`, `ExecutionPolicy`, `serving_effect`, `ServingContract`, audit) | 152 |
| G12 | `feed.py` `decider.py` + `tests/production/conftest.py` (real synthetic run) | 199 |
| G13 | `reconcile.py` `readiness.py` | 195 |
| G14 | `verifier.py` `executor.py` (+ conformance battery) | 453 |
| G15 | `monitors.py` | 188 |
| G16 | `alerts.py` `health.py` | ~345 |
| S0 | plan: phases 2/2b/3 specified to phase-1 standard; rulings R1–R28 folded; `check_plan.py` CLEAN | n/a |

Reviews done: **R1** foundations+ledger, **R2** seam+state (rulings R15–R17), **R3** authority stack+policy (R23–R24).

## REMAINING

1. **G17 `leg.py`** — red tests committed (`test_leg.py`, 138 cases). Build not started.
2. **G18 `compose.py` + `loop.py`** — tests not written.
3. **G19 `__main__.py` + e2e** — not started.
4. **G20 `test_oop.py` + `test_producers.py`** — not started.
5. **G21 docs/examples/skeleton** (§9.2, §9.3) — not started.
6. **Reviews outstanding**: guards+feed/decider; control/accounting+reconcile/readiness+monitors+alerts/health; the Stage 0 specification itself; then leg/compose/loop after they build.
7. **Phase 2, 2b, 3** — specified, not built.
8. **Wrap** — full suite, ADR-0090/0091 → accepted, TODO/README/CLAUDE/AGENTS trees, merge decision.

## Operating rules (owner, this session)
One subagent at a time; Opus only; commit and push after every unit of work with a status line.
