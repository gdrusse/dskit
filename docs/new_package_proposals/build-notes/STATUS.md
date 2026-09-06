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
| G17 | `leg.py` (LegPipeline, the eight steps, Authority family, ActPermit minting) | 138 |
| G18 | `compose.py` `loop.py` (bundles, AuthorityTable, handlers, ServeLoop, Tick) | 188 |
| G19 | `__main__.py` (17 verbs) + the shadow/paper/live_limited end-to-end | 177 |
| G20 | `test_oop.py` (§5.15) + `test_producers.py` (§5.16 closure) | 272 |
| G21 | docs: root README/CLAUDE/AGENTS, the package's three, examples, skeleton seam + pin | n/a |
| S0 | plan: phases 2/2b/3 specified to phase-1 standard; rulings R1–R28 folded; `check_plan.py` CLEAN | n/a |

Reviews done: **R1** foundations+ledger, **R2** seam+state (rulings R15-R17), **R3** authority stack+policy
(R23-R24), and the **consolidated pass** over every module the first three did not cover (2 blockers,
5 majors, 44 mutations run, 7 missed assertions given tests). All fixed and committed.

Production + pipeline: **6848 passed, 113 skipped**, one known root-only environment failure
(`test_runs.py::TestLostMeasurements::test_an_unlistable_nodes_dir_is_named_not_fatal`, uid 0).
ruff clean; five purity gates green; the 20 pinned sha256 literals byte-identical.

**Phase 1 is complete, reviewed, and documented.**

## REMAINING

1. **Plan gaps from the consolidated review** - in flight. Three safety-critical (the
   `safety_epoch_digest` recipe needs one owner and the gate must recompute it; `on_mismatch: refuse`
   has no mechanism; §5.13.1's `right` prose) and three bookkeeping (§6's `authority` id recipe, the
   two events with no phase-1 producer, the snapshot's added `last_trip` key).
2. **Phase 2 - evidence** (units 1-7 landed; only the docs pass remains):
   `outcomes.py` (§5.13.2) - `report.py` + the `replay` verb (§5.13.3) -
   outcome-readiness evidence (§5.13.4) - the Outcome and Parity monitor families and
   DDM/ADWIN/JensenShannon/LInf (§5.10.1) - alert inhibition, silences, escalation and `ack`
   (§5.11.2) - the systemd heartbeat - `libs/sqlite.py` (§5.8.2) - `libs/parquet.py` (§5.10.2) -
   `Signer` (§5.12.1) - `approve-hold` (§5.5.1) - the six §7 verbs and the two optional §4.1 sections.
3. **Phase 2b - audit**: classify the remaining registered `kinds_*.py` / `libs/*.py` classes for
   `serving_effect` by §9.1's four-step procedure. Touches only overrides and `tests/pipeline`.
4. **Phase 3 - packs**: `libs/exchange_calendars.py` (§5.1.1) - the `MetricSink` seam and
   `libs/prometheus.py` / `libs/opentelemetry.py` (§5.11.3) - the `websocket` stream seam (§5.2.1) -
   migrating the onboarding packs onto `resilience.py` under ADR-0092 (Appendix C).
5. **Wrap**: full suite, ruff, purity gates, hashes; ADR-0090/0091 -> accepted; refresh
   `docs/RE-ENTRY.md`; TODO.md; delete this `build-notes/` directory; merge decision.

## Operating rules (owner, this session)
One subagent at a time; Opus only; commit and push after every unit of work with a status line.
