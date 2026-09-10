## Skeptic review — Gate 5a replay conformance/coverage lens

**Reviewer task:** Gate 5a replay conformance / hook discovery, reviewer 1 of 2
**Model/effort:** Claude Sonnet 5
**Lens:** conformance/coverage only — does each of the six Gate 5a items have a
genuine synthetic, domain-blind test that would FAIL if the capability were
missing? Architecture/governance/tiering is explicitly out of scope for this
review (reviewer 2's lens).
**Dispatch mode:** sequential; this is an independent reviewer-owned report.

## Scope and reviewed state

Commit under review: `fbefa5d` ("test(production): Gate 5a replay conformance
/ hook discovery") on branch `cursor/gate5a-replay-conformance-1656`, parent
`95fe75d` on `origin/claude/phase1-recovery-seven-gates-ao4zdj`. The entire
diff is two files: `tests/production/test_executor.py` (+145 lines) and
`tests/production/test_loop.py` (+262 lines) — no production code changed.
Also read: `children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`
§6 Phase 5 and §11; ADR-0114 Phase 5 in `docs/architecture/decision-log.md:6420-6457`;
`dskit/production/{loop,feed,clock,executor,ledger,state,records}.py`;
`docs/skills/skeptic-review.md` Rules 1, 3, 7.

The implementer's claim (commit message): every Gate 5a item is
**PASS-EXISTING**, no production hook needed.

## Commands and results

```text
python3 -m pytest -q tests/production/test_loop.py tests/production/test_feed.py \
  tests/production/test_clock.py tests/production/test_executor.py \
  tests/production/test_ledger.py tests/production/test_purity.py tests/production/test_oop.py
# 769 passed, 12 skipped in 16.10s

python3 -m ruff check tests/production/test_loop.py tests/production/test_executor.py
# All checks passed!

git diff --check 95fe75d...HEAD
# exit 0, no output
```

All commands ran clean. This confirms the new tests pass and are style-clean —
it does **not** confirm they test what the commit claims. That gap is the
subject of this review.

## Findings

### Critical — Item 6 "crash/restart" test cannot fail; it never crashes or restarts anything

`tests/production/test_loop.py:3035-3082`
(`test_replaying_the_ledger_from_any_boundary_reproduces_the_uninterrupted_fold`).
The test builds one `JsonlLedger`/`SeriesState` (`state_full`), appends the
scenario, takes `reference = state_full.snapshot()` (`:3059`), then does
`envelopes = list(ledger.scan())` (`:3062`) **from the still-open ledger, in
the same process** — no close, no reopen, no new `InstanceLock` acquisition,
no `ChainLedger.reading`. For each `boundary`, it constructs **one** fresh
`SeriesState` object and, without ever discarding it, feeds it
`envelopes[:boundary]` (`:3064-3065`) immediately followed by
`envelopes[boundary:]` (`:3067-3068`) on the **same object, same call stack,
same test invocation**. Nothing is ever lost, closed, or reopened.

`SeriesState.apply` (`dskit/production/state.py:1036-1084`) is a pure fold:
its output for a given envelope depends only on the envelope and the fold's
own prior internal state (no clock, no I/O, no randomness — the package's own
`AGENTS.md` states "nothing outside `clock.py` calls `time.time()`"). Because
Python slicing guarantees `envelopes[:boundary] + envelopes[boundary:] ==
envelopes`, applying the two slices in sequence to one object is
**definitionally identical** to applying the whole list in one loop, for
every `boundary` in `range(len(envelopes) + 1)`. There is no way to make this
assertion fail short of breaking `apply`'s per-envelope determinism, which no
crash/restart defect would ever touch — a genuinely broken checkpoint/recovery
path (e.g. `Recovery` never re-issuing `cancel_all`, a fresh process reading a
truncated tail, a lock not being released/reacquired) would sail through this
test untouched, because no process boundary, no disk re-read, and no lock
handoff is ever exercised.

The test's own docstring (`:3044-3049`) asserts "Splitting is the simulated
crash: the first loop's `restarted` state is abandoned (RAM lost)... only the
disk-persisted envelopes... survive to feed the second loop — equivalent to a
real process kill and restart." This is false as written: there is no "first
loop's `restarted`" object distinct from the second; it is one object used for
both slices, and its RAM is never abandoned.

This is exactly the tautological split the review brief warned against ("a
test that cannot fail is a Major" at minimum). Given item 6 is the single
most safety-critical claim in Gate 5a — that a killed/restarted trading
process reconstructs identical positions, cash, NAV, and metrics — and the
existing repository already has a real reopen-from-disk pattern available
(`tests/production/test_ledger.py:539-557` `open_ledger`/reopen fixtures) that
this test does not use, I rate this **Critical**, not Major.

Additionally: the assertions (`:3070-3075`) compare `positions`, `balances`,
`working`, `pending`, and `risk_version.economic_seq` only. **NAV and metrics,
both named explicitly by the plan** ("Replaying the ledger must yield
identical positions, cash, NAV, and metrics" — plan §6 Phase 5 item 3) and by
the Gate 5a briefing, are never computed or compared anywhere in this test.
`StateView` (`dskit/production/state.py:1487-1506`) itself carries no `nav`
field, so proving NAV/metrics identity would require going through
`report.py`, which this commit does not touch and this test does not invoke.

### Critical — Item 4 "forced exit": no real test exists; the cited analogy is a different mechanism

`tests/production/test_executor.py:1731-1739` (comment) claims: "A forced
exit is already injectable as a `gtd`/`day` TIF
(`test_a_gtd_order_expires_at_the_proposals_expiry`,
`test_day_expires_at_the_declared_session_end`) — no new test needed for that
half of the claim."

Both cited tests (`tests/production/test_executor.py:924-938` and `:967-983`)
exercise TIF **expiry of an unfilled resting order** — the order transitions
to `"expired"` and is removed from `open_orders()`. Neither test involves an
open **position** at all: no fill precedes the expiry in either test, so
there is nothing to "exit." Gate 5a item 4 and the plan's §11 item 6 both use
"forced exit" to mean closing an **already-filled position** after a holding
limit (e.g., an end-of-day flatten) — a fundamentally different operation
from cancelling a never-filled order. Order-TIF-expiry cannot serve as
evidence for position-forced-exit: there is no generic hook, no test, and no
assertion anywhere in this commit (or cited from elsewhere) that a caller can
inject a rule that closes an open position after a tracked holding duration.

This is a required Gate 5a item with **zero real passing test**, papered over
by an analogy to an unrelated mechanism — worse than an honestly documented
gap, because the commit message and in-file comment both assert coverage
("no new test needed") rather than naming a gap. It also quietly answers part
of §11 item 6 (fill model: "...forced exits") — which ADR-0114 explicitly says
is still blocked pending an owner ruling (`docs/architecture/decision-log.md:6447-6450`)
— by implying TIF expiry already satisfies it. The commit message's claim
"does not infer §11 items 5 or 6" is not accurate for this half of item 4.

### Major — Item 1 "no lookahead" is proven only of a test-local decider with a fake feed, never of the real `ReplayFeed`/`ReplayClock`

The two new item-1 tests
(`tests/production/test_loop.py:2905-2921` and `:2923-2938`) both call
`make_harness(tmp_path, serve_document, release_manifest, clock, calls, ...)`,
which builds a `Harness` (`tests/production/test_loop.py:748-849`) whose
`"feed"` default is `FakeFeed(calls)` (`tests/production/test_loop.py:238-253`,
instantiated at `:772`) — **not** `dskit.production.feed.ReplayFeed`. Neither
test passes a `feed=` override, so `ReplayFeed` is never instantiated in
either test. The no-lookahead filtering itself is performed entirely inside
the test's own `AsOfBarDecider.read_entry` (`tests/production/test_loop.py:2866-2889`,
filter at `:2879-2881`: `visible = tuple(bar for bar in self._bars if bar[0] <=
tick_at_ms)`) — a decider the test authors wrote to filter by construction.
What is actually proven is that `Tick`/`Data` thread one `tick_at_ms` value
unmolested into both `feed.pull` and `decider.read_entry`
(`:2913-2914`) — a real and useful plumbing fact, but a materially weaker
claim than "existing feed/clock correctly expose only data known-at the
current replay instant," which is how the commit's own file-header comment
states item 1 (`tests/production/test_loop.py:2825-2826`, "existing
`ServeLoop` + `ReplayFeed`/`ReplayClock` + `PaperExecutor` + the ledger").

The one pre-existing test cited as already covering the rest of the claim,
`tests/production/test_feed.py:828-838`
(`test_a_pull_moves_the_instant_the_replay_clock_shares`), asserts that
`ReplayClock.now_ms()` advances by one unit each time `ReplayFeed.pull` is
called — a clock-tick-source fact, unrelated to whether `ReplayFeed` filters
*which bars/records* are visible at a given instant. It does not exercise
bar/record visibility at all (the tape entries in `taped()` carry only a
`status`, no dated records to filter). So neither the new tests nor the cited
existing test demonstrate that the actual `ReplayFeed`/`ReplayClock`
implementations enforce (or even can express) no-lookahead over dated
records; only a test-author-supplied decider's own filter is shown to be
threaded correctly.

### Major — No test drives `ServeLoop` + `ReplayFeed` + `ReplayClock` + `PaperExecutor` + the ledger together, as ADR-0114 explicitly asks

ADR-0114 states the Gate 5a deliverable in these words: "Prove by test
whether existing `ServeLoop` + replay feed/clock + paper executor can drive
deterministic historical ticks" (`docs/architecture/decision-log.md:6428-6430`).
Searching the full diff and the wider `tests/production/` tree:

- `ReplayFeed`/`ReplayClock` are named in `tests/production/test_loop.py`
  **only inside comments** (`:2825`, `:2836`) — never instantiated there.
- `PaperExecutor` is named in `tests/production/test_loop.py` only inside
  comments (`:2825`, `:2832`) — never instantiated there.
- `ServeLoop` does not appear anywhere in `tests/production/test_feed.py` or
  `tests/production/test_executor.py`.
- `tests/production/test_compose.py:671` only asserts that the `"paper"`
  registry key resolves to the `PaperExecutor` class — it does not run a tick.

Every Gate 5a assertion in this commit exercises exactly one seam at a time
against a fake/stand-in for its neighbors: `test_loop.py`'s new tests drive
`Tick`/`Data` against `FakeFeed` + a custom decider; `test_executor.py`'s new
tests drive `PaperExecutor` alone; the crash/restart test drives
`SeriesState`/`JsonlLedger` alone, bypassing `ServeLoop`, `ReplayFeed`, and
`PaperExecutor` entirely (envelopes are hand-built dicts, not the product of
an executed tick). No test anywhere constructs `ReplayFeed` and `ReplayClock`
and `PaperExecutor` and drives them through one `ServeLoop`/`Tick` to produce
a replayed historical sequence of ticks. The "PASS-EXISTING, no hook needed"
verdict for Gate 5a's central deliverable therefore rests entirely on
component-level isolation tests, not on the integrated conformance run the
ADR names.

## Items not found defective

- **Item 2** (order/fill timestamps, price, fees): genuinely tested.
  `tests/production/test_executor.py:1746-1757` and `:1760-1776` both make
  real, falsifiable assertions (clock-instant equality, byte-identical
  two-run replay) against the real `PaperExecutor`.
- **Item 3** (rejection/partial-fill):
  `tests/production/test_executor.py:1779-1802` makes four real, falsifiable
  assertions against the real `PaperExecutor` (`no_quote` reject, `size_cap`
  partial, all-or-nothing, `fok`). Not tautological.
- **Item 4, holding-clock half**: `tests/production/test_executor.py:1806-1832`
  is a real test — it would fail if `Fill.ts_ms` or `OrderState.created_ms`
  were not correctly clock-derived. (Only the forced-exit half is defective —
  see Critical finding above.)
- **Item 5** (overlapping signals): `tests/production/test_executor.py:1834-1847`
  honestly documents current behavior only and explicitly declines to decide
  policy ("this task does not decide whether one is needed" — `:1741-1743`).
  No §11 inference here.

## Disposition

Two Critical and two Major findings. Per this lens's bar (zero Critical, zero
Major to pass), this fails.

**Verbatim verdict:** `FAIL — 2 Critical, 2 Major.`
