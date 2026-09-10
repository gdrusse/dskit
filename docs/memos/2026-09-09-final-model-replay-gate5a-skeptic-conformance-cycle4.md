## Skeptic review — Gate 5a replay conformance/coverage lens (cycle 4)

**Reviewer task:** Gate 5a replay conformance / hook discovery, reviewer 1 of 2
**Model/effort:** Claude Sonnet 5
**Lens:** conformance/coverage only — does each of the six Gate 5a items have a
genuine synthetic, domain-blind test that would FAIL if the capability were
missing? Architecture/governance/tiering is explicitly out of scope for this
review (reviewer 2's lens).
**Dispatch mode:** sequential; this is an independent reviewer-owned report.

## Scope and reviewed state

Commit under review: `973387d` ("test(production): Gate 5a cycle 3 — pin
working/pending at intent") on branch `cursor/gate5a-replay-conformance-1656`,
parent `a5644ca` (journal A18856), grandparent `9ebdb97` (the cycle-3 FAIL
memo commit), great-grandparent `b25159f` (cycle 2's fix). `git show 973387d
--stat` confirms the diff touches exactly one file —
`tests/production/test_loop.py` (+17/-6 lines within
`test_replaying_the_ledger_from_any_boundary_reproduces_the_uninterrupted_fold`
and its docstring). `git diff eb61aa0..HEAD -- tests/production/test_executor.py
tests/production/test_feed.py tests/production/test_clock.py` is empty — no
production code and none of the other three test files changed across cycles
2, 3 or 4. The commit message claims: "At a pending-intent crash boundary
Recovery's unknown order_event also moves the ref pending→working. Assert
n_working +N and n_pending -N alongside economic_seq, matching CURRENT
`_fold_order_event`."

Also read: the cycle-1, cycle-2 and cycle-3 FAIL memos in full;
`dskit/production/state.py` (`SeriesState._fold_order_event`,
`vocab.TERMINAL_STATUSES`, `Recovery.run`/`_close_ticks`/`_query`,
`_fold_metrics`'s four fields via `test_loop.py`); `tests/production/
test_loop.py` around `_crash_restart_scenario`,
`_write_crash_records`/`_reopen_and_recover`, and the test under review in
full.

## Commands and results

```text
python3 -m pytest -q tests/production/test_loop.py tests/production/test_feed.py \
  tests/production/test_clock.py tests/production/test_executor.py \
  tests/production/test_ledger.py tests/production/test_purity.py tests/production/test_oop.py
# 775 passed, 12 skipped in 16.13s

python3 -m ruff check tests/production/test_loop.py tests/production/test_executor.py \
  tests/production/test_feed.py tests/production/test_clock.py
# All checks passed!

git diff --check 95fe75d...HEAD
# exit 0, no output
```

As in every prior cycle, this confirms the tests pass and are style-clean —
it does not confirm they test what is claimed. I reproduced the crash/restart
scenario directly against the production modules outside pytest (a scratch
script at `/workspace/scratch_repro_gate5a.py`, since deleted, importing
`tests.production.test_loop` as a module and calling `_crash_restart_scenario`,
`_write_crash_records`, `_reopen_and_recover` and `_economic` directly) at all
three pending-intent boundaries, to check the cycle-3 Major's fix empirically
rather than trust the commit message.

## Findings — cycle-3's Major, re-verified

Cycle 3's Major was: the fix pinned `economic_seq` at a pending-intent
boundary but left `n_working`/`n_pending` divergence at the same boundary
unasserted, despite the docstring claiming "the assertions below pin that
divergence." This commit adds three new assertions inside the `if
pending_before:` branch (`test_loop.py:3170-3180`):

```python
n_pending = len(pending_before)
assert got["metrics"]["economic_seq"] == (
    expected["metrics"]["economic_seq"] + n_pending
)
assert got["metrics"]["n_working"] == (
    expected["metrics"]["n_working"] + n_pending
)
assert got["metrics"]["n_pending"] == (
    expected["metrics"]["n_pending"] - n_pending
)
assert got["metrics"]["n_positions"] == expected["metrics"]["n_positions"]
```

and rewrites the docstring to name a "three-field divergence (`economic_seq`
+1, `n_working` +1, `n_pending` -1 per pending ref)" and to describe the
mechanism precisely: Recovery's `_query` records an `unknown` `order_event`;
`_fold_order_event` (`state.py:1192-1217`) unconditionally pops the ref from
`_pending` and, because `"unknown"` is not in `vocab.TERMINAL_STATUSES`
(confirmed: `("filled", "cancelled", "expired", "rejected", "replaced",
"not_sent")`, `vocab.py:151`), adds it to `_working`.

I independently reproduced all three pending-intent boundaries directly
against the live production modules (not through pytest):

```text
bound=5  kind=intent rid=intent-buy
  expected metrics = {'economic_seq': 1, 'n_positions': 0, 'n_working': 0, 'n_pending': 1}
  got      metrics = {'economic_seq': 2, 'n_positions': 0, 'n_working': 1, 'n_pending': 0}
  MATCH: economic_seq=True n_working=True n_pending=True n_positions=True
  positions/balances/nav match: True/True/True

bound=11 kind=intent rid=intent-reduce
  expected metrics = {'economic_seq': 5, 'n_positions': 1, 'n_working': 0, 'n_pending': 1}
  got      metrics = {'economic_seq': 6, 'n_positions': 1, 'n_working': 1, 'n_pending': 0}
  MATCH: economic_seq=True n_working=True n_pending=True n_positions=True
  positions/balances/nav match: True/True/True

bound=16 kind=intent rid=intent-close
  expected metrics = {'economic_seq': 9, 'n_positions': 1, 'n_working': 0, 'n_pending': 1}
  got      metrics = {'economic_seq': 10,'n_positions': 1, 'n_working': 1, 'n_pending': 0}
  MATCH: economic_seq=True n_working=True n_pending=True n_positions=True
  positions/balances/nav match: True/True/True
```

This confirms the code's actual behaviour matches the docstring's claim
exactly, at all three named pending-intent boundaries the scenario exercises,
and all four `_fold_metrics()` fields (`economic_seq`, `n_positions`,
`n_working`, `n_pending`) — not just one — are now asserted at the pending
boundary, closing cycle 3's specific complaint that two of the four fields
were silently divergent and unpinned.

**Falsifiability.** I monkeypatched `Recovery._query` to a no-op (simulating
the query-and-record capability being removed, so the pending ref is never
touched and no `order_event` is appended) and re-ran all three boundaries
through the real `Recovery.run`:

```text
bound=5  rid=intent-buy    with no-op _query: got={'economic_seq': 1, 'n_positions': 0, 'n_working': 0, 'n_pending': 1}
  -> assertion_would_fail(seq=True, working=True, pending=True)
bound=11 rid=intent-reduce with no-op _query: got={'economic_seq': 5, 'n_positions': 1, 'n_working': 0, 'n_pending': 1}
  -> assertion_would_fail(seq=True, working=True, pending=True)
bound=16 rid=intent-close  with no-op _query: got={'economic_seq': 9, 'n_positions': 1, 'n_working': 0, 'n_pending': 1}
  -> assertion_would_fail(seq=True, working=True, pending=True)
```

At every boundary, each of the three new assertions (`economic_seq`,
`n_working`, `n_pending`) independently fails when Recovery's query-and-fold
step is removed — none of them is tautological or vacuously true. The fix is
genuine and falsifiable, not just a docstring restatement.

## Nit

The new formula (`+ n_pending`, `- n_pending`) is written generically for
however many refs are pending at a boundary, but `_crash_restart_scenario`
never has more than one ref pending at any named boundary (confirmed: `bound=5,
11, 16` each have exactly one entry in `pending_before`). The generalization
to simultaneous multi-ref pending intents is algebraically justified by
reading `_fold_order_event` (it processes exactly one ref per `order_event`,
regardless of how many other refs are pending) but is not empirically
exercised by this suite. This does not affect the pass bar — the scenario
matches what item 6's crash boundaries name (one order per named kind) — but
is worth naming for a future reviewer who wants to strengthen the scenario.

## Items not found defective

Re-verified by direct reproduction and/or reading, not only by diffing:

- **The cycle-3 Major (the two-thirds-unpinned pending-boundary
  divergence):** genuinely closed — see above.
- **Items 1–5 and the ServeLoop integration** (no-lookahead over the real
  `ReplayFeed`/`ReplayClock`, order/fill fidelity, rejection/partial-fill,
  caller-injected forced exit of a filled position, overlapping-signals
  documented-current-behaviour, the integrated
  `ServeLoop`+`ReplayFeed`+`ReplayClock`+`PaperExecutor`+ledger determinism
  test): unchanged by any commit since cycle 2 (`git diff eb61aa0..HEAD --
  tests/production/test_executor.py tests/production/test_feed.py
  tests/production/test_clock.py` is empty). Spot-verified by re-reading, not
  only trusting the empty diff: `test_a_tick_over_replay_feed_and_replay_clock_
  keeps_later_tape_unconsumed` and `test_a_caller_decider_can_enforce_no_
  lookahead_from_the_threaded_tick_at_ms` (item 1) construct the real
  `ReplayFeed`/`ReplayClock` and drive a synthetic three-entry tape;
  `test_serve_loop_with_replay_feed_clock_paper_executor_and_ledger_is_
  deterministic` (`test_loop.py:3336-3419`) constructs a real `ServeLoop` +
  `ReplayFeed` + `ReplayClock` + `PaperExecutor` (`_paper_venue`) + real
  `LegPipeline`/`SimulatedAuthority`, runs it twice from identical seeds, and
  asserts identical fills/acks — not tautological, since it asserts
  `first[1]` (the fill list) is non-empty, so an executor that silently
  dropped fills would fail it. `test_executor.py:1832`
  (`test_a_caller_can_force_exit_a_filled_position_after_a_holding_
  duration`) is explicitly the caller-advances-the-clock mechanism named
  item 4 in the file's own comment block (`:1731-1736`), distinct from TIF
  expiry of an unfilled order. `test_executor.py:1865`
  (overlapping-signals) and its neighbouring comment (`:1738`,
  `:1865-1868`) document current `PaperExecutor` behaviour only and
  explicitly disclaim any adapter-level policy opinion. Rejection/partial-fill
  (`test_a_submit_before_any_quote_is_rejected_not_guessed`,
  `test_size_cap_frac_delivers_a_partial_fill`,
  `test_partial_fills_false_fills_all_or_nothing`,
  `test_an_ioc_that_fills_partially_cancels_the_remainder`) exercise existing
  caller-supplied knobs against `assume_complete_fill` fixtures — no
  domain-specific fill model. I re-ran the full listed suite (775 passed, 12
  skipped, identical to cycle 3) rather than relying on a stale run.
- **Purity/OOP gates** (`test_purity.py`, `test_oop.py`): pass, unchanged;
  out of this lens's scope in any case (reviewer 2's).

## Disposition

Zero Critical, zero Major, one Nit. Cycle 3's Major — the pending-intent
boundary's `n_working`/`n_pending` divergence being surfaced by the docstring
but left unasserted — is now genuinely fixed: all four `_fold_metrics()`
fields are asserted at the pending boundary, the assertions are independently
falsifiable (each fails on its own when `Recovery._query` is neutered), and I
reproduced the exact numbers directly against the production modules rather
than trusting the commit message or the docstring's prose. Items 1–5 and the
ServeLoop integration remain genuine on re-inspection. This lens's bar (zero
Critical, zero Major) is met.

**Verbatim verdict:** `PASS — 0 Critical, 0 Major, 1 Nit.`
