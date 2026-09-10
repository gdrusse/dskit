## Skeptic review — Gate 5a replay conformance/coverage lens (cycle 2)

**Reviewer task:** Gate 5a replay conformance / hook discovery, reviewer 1 of 2
**Model/effort:** Claude Sonnet 5
**Lens:** conformance/coverage only — does each of the six Gate 5a items have a
genuine synthetic, domain-blind test that would FAIL if the capability were
missing? Architecture/governance/tiering is explicitly out of scope for this
review (reviewer 2's lens).
**Dispatch mode:** sequential; this is an independent reviewer-owned report.

## Scope and reviewed state

Commit under review: `c3ff034` ("test(production): Gate 5a cycle 1 —
crash/restart, forced exit, ReplayFeed") on branch
`cursor/gate5a-replay-conformance-1656`, parent `9584bae` (the cycle-1 FAIL
memo commit), grandparent `fbefa5d` (the original Gate 5a commit), base
`95fe75d` on `origin/claude/phase1-recovery-seven-gates-ao4zdj`. The diff
`9584bae..c3ff034` touches exactly four files — `tests/production/{test_clock,
test_executor, test_feed, test_loop}.py` (+493/-92 lines) — no production code
changed, confirmed by `git show c3ff034 --stat` and `git diff 95fe75d..c3ff034
--stat` (only the four test files plus the cycle-1 memo doc). The commit
message claims this cycle closes cycle 1's four findings ("2 Critical, 2
Major") with tests only.

Also read: the cycle-1 FAIL memo in full
(`docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance.md`);
`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`
§6 Phase 5 items 1 and 3 (`:363-373`, "Test crash/restart at every boundary:
cash flow, decision, intent, fill, outcome, exit, and checkpoint. Replaying
the ledger must yield identical positions, cash, NAV, and metrics."); ADR-0114
Phase 5 (`docs/architecture/decision-log.md:6420-6457`);
`dskit/production/{ledger,state,accounting}.py` (`JsonlLedger`'s on-disk
open/close/lock mechanics, `Recovery.run`/`_replay`, `SeriesState`'s
`economic_seq` rule at `state.py:35-38`).

## Commands and results

```text
python3 -m pytest -q tests/production/test_loop.py tests/production/test_feed.py \
  tests/production/test_clock.py tests/production/test_executor.py \
  tests/production/test_ledger.py tests/production/test_purity.py tests/production/test_oop.py
# 775 passed, 12 skipped in 16.09s

python3 -m ruff check tests/production/test_loop.py tests/production/test_executor.py \
  tests/production/test_feed.py tests/production/test_clock.py
# All checks passed!

git diff --check 95fe75d...HEAD
# exit 0, no output
```

All commands ran clean. As before, this confirms the tests pass and are
style-clean — it does not by itself confirm they test what is claimed. Beyond
reading the diff, I reproduced key scenarios directly against the production
modules (outside pytest, via a scratch script since deleted) to check claims
empirically rather than by inspection alone; results are reported inline below.

## Findings

### Major — the crash/restart test's own docstring claims "economic_seq must still agree" at a pending-intent boundary; the code skips exactly that check, and the claim is demonstrably false

`tests/production/test_loop.py:3128-3184`
(`test_replaying_the_ledger_from_any_boundary_reproduces_the_uninterrupted_fold`).
The docstring (`:3131-3137`) says: "Recovery may append a `recovered` process
record (and, at a pending-intent boundary, an `unknown` order_event) — those
are not economic; positions, cash, NAV **and economic_seq must still agree**."
The code immediately contradicts this:

```python
if not pending_before:
    assert got["metrics"]["economic_seq"] == expected["metrics"]["economic_seq"]
if not pending_before and not open_ticks_before:
    assert got["metrics"]["n_working"] == expected["metrics"]["n_working"]
    assert got["metrics"]["n_pending"] == expected["metrics"]["n_pending"]
```

`economic_seq` is checked **only when there is no pending intent** — i.e.
never at exactly the "pending-intent boundary" the docstring calls out. I
reproduced the three `intent` boundaries (`intent-buy`, `intent-reduce`,
`intent-close`) directly against `_write_crash_records`/`_reopen_and_recover`
outside pytest: at every one, `expected["metrics"]["economic_seq"]` is `1`
(the uninterrupted fold, no `order_event` recorded yet) while
`got["metrics"]["economic_seq"]` after close/reopen/Recovery is `2` — Recovery
appends an `unknown` `order_event` for the unresolved intent (`state.py:1958`
querying `_SilentExecutor.order`, which always returns `None`), and
`order_event` is one of the exactly-three kinds `state.py:35-38` documents as
advancing `economic_seq` ("`economic_seq` advances on exactly three kinds —
`order_event`, `fill` and `cash_flow`"). So the docstring's claim is not an
edge case correctly handled; it is **false as stated**, the divergence is
real and mechanical, and the guard exists precisely because checking it would
fail the test. Removing the `if not pending_before:` guard turns this into a
failing assertion (`1 != 2`), confirmed by direct reproduction.

The `n_working`/`n_pending` half is weaker still: `open_ticks_before` is
`True` at **every** named boundary except the final checkpoint (`tick-1` only
terminates near the end of the scenario), so across the 13 named boundaries
this test exercises, `n_working`/`n_pending` identity is actually asserted at
exactly **one** of them. `economic_seq` fares better (checked at 10 of 13 —
skipped only at the 3 `intent` boundaries) but is skipped precisely where the
prose says it should hold.

Plan §6 Phase 5 item 3 requires, "at every boundary," that "positions, cash,
NAV, and metrics" all come back identical after a crash. `positions`,
`balances` and `nav` genuinely do (verified below) at all 13 boundaries — that
part of item 6 is solid, and this is a real improvement over cycle 1. But the
"metrics" component of the same requirement is asserted at only a fraction of
the boundaries the test claims to cover, the one boundary where the test's
own comment names the interesting case is exactly where the assertion is
disabled, and the claim in prose is contradicted by the claim in code. This
is the "weaker claim than advertised" pattern the review brief names as at
least a Major.

### Items not found defective — cycle 1's four findings genuinely closed

I re-verified all four with reproduction, not just reading, given cycle 1's
own docstring turned out to misdescribe its test:

- **Item 6, positions/balances/NAV (was Critical: tautological, same-object
  split, no reopen, no NAV):** `_write_crash_records` writes to a real
  `JsonlLedger` (`O_APPEND`, `fsync`, a real `serve.lock` `flock` acquired
  per-open per `ledger.py:949-978`) and `_reopen_and_recover` constructs a
  **new** `SeriesState` and a **new** `JsonlLedger` against the same
  `ServeRoot` after `ledger.close()` releases the lock — genuinely closing,
  abandoning the in-RAM fold, and reopening from disk, unlike cycle 1's
  same-object double-slice. I reproduced the `fill-buy` boundary directly:
  the uninterrupted fold holds `Position(instrument='INS1', qty=5,
  avg_cost=10, ...)` and balance `{'USD': 100000}`; after close/reopen/
  Recovery the restored fold matches exactly, both non-trivially. NAV is
  genuinely computed via `PaperAccounting.value` (`_nav_of`, `:3004-3009`)
  and is **not** an alias for cash balance — I reproduced a boundary where
  balance is `100000` but NAV is `100052.5` (the open position marked to the
  quote set), so the NAV comparison is a real, distinct claim, not
  tautologically equal to the balance check beside it. This closes cycle 1's
  Critical cleanly for positions/balances/NAV; only the metrics half retains
  the gap above.
- **Item 4, forced exit (was Critical: only a TIF-expiry analogy, no real
  test):** `tests/production/test_executor.py:1832-1861`
  (`test_a_caller_can_force_exit_a_filled_position_after_a_holding_duration`)
  now opens a real filled position via `PaperExecutor.submit`, advances a
  `TestClock` by a holding duration, submits a real opposite-side `sell`/
  `ioc` close through the same venue, and asserts (a) the close actually
  fills, (b) `PositionBook.net_qty` nets to zero, and (c) the fill-to-fill gap
  equals the injected holding duration. This is a real, falsifiable
  assertion against the real `PaperExecutor` and correctly separates the
  mechanism from cycle 1's TIF-expiry-of-an-unfilled-order analogy (the
  header comment now explicitly disclaims that conflation,
  `test_executor.py:1734-1737`). It is a fairly weak distinguishing test in
  the sense that nothing about it differs mechanically from an ordinary
  close order — item 4 itself defines "forced exit" as caller-injected, not
  an executor policy, so this is the correct shape of evidence for that
  definition, not a defect.
- **Item 1, real `ReplayFeed`/`ReplayClock` (was Major: only a fake feed with
  a test-authored filter):** `tests/production/test_feed.py:840-866` and
  `tests/production/test_clock.py:207-226` now construct the real
  `ReplayFeed`/`ReplayClock` and pin their actual current behaviour (ignores
  `tick_at_ms`, serves the tape in order, advances the shared clock to the
  entry's own `at_ms`). `tests/production/test_loop.py:3187-3223`
  (`test_a_tick_over_replay_feed_and_replay_clock_keeps_later_tape_unconsumed`)
  drives this through a real `Tick.run` with the real objects rather than
  `FakeFeed`, and shows a caller decider can still enforce no-lookahead from
  the threaded `tick_at_ms` regardless of what the feed itself returns. The
  claim is honestly scoped ("CURRENT" behaviour, mechanism only) and matches
  what item 1 asks.
- **No integrated `ServeLoop` + `ReplayFeed` + `ReplayClock` + `PaperExecutor`
  + ledger run (was Major):**
  `tests/production/test_loop.py:3311-3394`
  (`test_serve_loop_with_replay_feed_clock_paper_executor_and_ledger_is_deterministic`)
  now constructs all five together — real `ReplayFeed`, real `ReplayClock`,
  real `PaperExecutor`, real `JsonlLedger`, and the real `LegPipeline`/
  `SimulatedAuthority` (guards/arming/accounting/proposer are faked, which is
  in scope: ADR-0114's own wording names only "`ServeLoop` + replay feed/clock
  + paper executor"). Two independent runs from identical inputs are asserted
  byte-identical on exit code, fills and order-event acks, with an assertion
  message that fails loudly (naming ticks/decisions/plans) if no fill were
  ever produced — not a vacuous pass. `monkeypatch.setattr(loop_module,
  "LegPipeline", LegPipeline)` sets the module attribute to the class already
  imported at `loop.py`'s top level (confirmed by grep — no other test in the
  file leaves it patched to a fake by the time this test runs, since
  `monkeypatch` auto-reverts per test); it is defensive, not a fake swap.

### Nit — a duplicated boundary test and dead-code OR clause

`tests/production/test_feed.py:840-866` and `tests/production/test_clock.py
:207-226` assert the near-identical scenario (tape ignored by `tick_at_ms`,
clock follows tape order) twice, once per module's perspective — acceptable
but worth naming as duplication rather than two independent proofs. Separately,
`tests/production/test_loop.py:3145` ORs in `(kind == "fill" and rid ==
"fill-close")` to select the "exit" boundary, but `"fill"` is already a member
of `_NAMED_BOUNDARIES` (`:2979-2981`), so every fill — including
`fill-close` — is already selected; the clause is dead code, harmless but
confusing about which boundary is meant to represent plan item 6's "exit".

## Disposition

Zero Critical, one Major (the crash/restart test's `economic_seq`/metrics
claim is stated more strongly in prose than the code verifies, and the case
the prose calls out by name is demonstrably false when checked directly).
Cycle 1's two Critical and one of its two Major findings are genuinely
closed with real, falsifiable, non-tautological tests against the real
production machinery, confirmed by direct reproduction, not just reading.
Per this lens's bar (zero Critical, zero Major to pass), this still fails.

**Verbatim verdict:** `FAIL — 0 Critical, 1 Major, 1 Nit.`
