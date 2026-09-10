## Skeptic review — Gate 5a replay conformance/coverage lens (cycle 3)

**Reviewer task:** Gate 5a replay conformance / hook discovery, reviewer 1 of 2
**Model/effort:** Claude Sonnet 5
**Lens:** conformance/coverage only — does each of the six Gate 5a items have a
genuine synthetic, domain-blind test that would FAIL if the capability were
missing? Architecture/governance/tiering is explicitly out of scope for this
review (reviewer 2's lens).
**Dispatch mode:** sequential; this is an independent reviewer-owned report.

## Scope and reviewed state

Commit under review: `b25159f` ("test(production): Gate 5a cycle 2 — pin
CURRENT Recovery metrics at intent") on branch
`cursor/gate5a-replay-conformance-1656`, parent `eb61aa0` (the cycle-2 FAIL
memo commit), grandparent `c3ff034` (cycle 1's fix commit). `git show b25159f
--stat` confirms the diff touches exactly one file —
`tests/production/test_loop.py` (+39/-25 lines, entirely inside
`_crash_restart_scenario` and
`test_replaying_the_ledger_from_any_boundary_reproduces_the_uninterrupted_fold`)
— no production code changed, and `test_executor.py`/`test_feed.py`/
`test_clock.py` are byte-identical to cycle 2 (`git diff eb61aa0..b25159f --
tests/production/test_executor.py tests/production/test_feed.py
tests/production/test_clock.py` is empty). The commit message claims: "Honest
crash/restart assertions: positions/cash/NAV always; full fold metrics when
nothing is pending; at a pending-intent boundary Recovery's unknown
order_event advances economic_seq and is asserted, not skipped."

Also read: the cycle-1 and cycle-2 FAIL memos in full; `children/
intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`
§6 Phase 5 item 3 ("Replaying the ledger must yield identical positions,
cash, NAV, and metrics"); ADR-0114 Phase 5; `dskit/production/state.py`
(`SeriesState._fold_intent`/`_fold_order_event`/`_fold_tick`/`_fold_decision`,
`Recovery.run`/`_close_ticks`/`_query`, the `economic_seq` rule at
`state.py:35-38`, `vocab.TERMINAL_STATUSES`).

## Commands and results

```text
python3 -m pytest -q tests/production/test_loop.py tests/production/test_feed.py \
  tests/production/test_clock.py tests/production/test_executor.py \
  tests/production/test_ledger.py tests/production/test_purity.py tests/production/test_oop.py
# 775 passed, 12 skipped in 15.87s

python3 -m ruff check tests/production/test_loop.py tests/production/test_executor.py \
  tests/production/test_feed.py tests/production/test_clock.py
# All checks passed!

git diff --check 95fe75d...HEAD
# exit 0, no output
```

As in both prior cycles, this confirms the tests pass and are style-clean —
it does not confirm they test what is claimed. I reproduced the crash/restart
scenario directly against the production modules outside pytest (scratch
scripts, since deleted) at every named boundary to check the claims
empirically; results reported inline below.

## Findings

### Major — the fix pins `economic_seq` at the pending-intent boundary but leaves `n_working`/`n_pending` divergence at the SAME boundary unasserted and undisclosed, despite the plan requiring "metrics" (not one field) to be accounted for

Cycle 2's Major was that the docstring claimed `economic_seq` identity at a
pending-intent boundary while the code skipped exactly that check. This
commit closes that specific lie: I reproduced all three `intent` boundaries
(`intent-buy`, `intent-reduce`, `intent-close`) directly against
`_write_crash_records`/`_reopen_and_recover`, and confirmed the new code's
claim is now true and falsifiable — monkeypatching `Recovery._query` to a
no-op (simulating the capability being removed) makes
`test_loop.py:3167-3169`'s assertion fail with an empty error (`1 != 2`),
confirmed by direct reproduction. That part of the fix is genuine.

But `SeriesState._fold_order_event` (`state.py:1192-1217`) unconditionally
pops the ref from `_pending` (`:1212`, runs regardless of status) and, because
`"unknown"` is not in `vocab.TERMINAL_STATUSES` (`("filled", "cancelled",
"expired", "rejected", "replaced", "not_sent")`), adds it to `_working`
(`:1216-1217`). So Recovery's `unknown` `order_event` at a pending-intent
boundary does not just advance `economic_seq` — it also flips `n_pending`
1→0 and `n_working` 0→1. I reproduced this directly for all three pending
boundaries:

```text
bound=5  kind=intent rid=intent-buy    expected={'economic_seq': 1, 'n_positions': 0, 'n_working': 0, 'n_pending': 1}
                                       got     ={'economic_seq': 2, 'n_positions': 0, 'n_working': 1, 'n_pending': 0}
bound=11 kind=intent rid=intent-reduce expected={'economic_seq': 5, 'n_positions': 1, 'n_working': 0, 'n_pending': 1}
                                       got     ={'economic_seq': 6, 'n_positions': 1, 'n_working': 1, 'n_pending': 0}
bound=16 kind=intent rid=intent-close  expected={'economic_seq': 9, 'n_positions': 1, 'n_working': 0, 'n_pending': 1}
                                       got     ={'economic_seq': 10,'n_positions': 1, 'n_working': 1, 'n_pending': 0}
```

The `if pending_before:` branch (`test_loop.py:3164-3176`) asserts `kind`,
`economic_seq`, `queried_refs` and the recovered `order_event` records — but
never touches `n_working` or `n_pending` at all, in either direction. This is
not a case of the test correctly declining to assert an unrelated fact: the
test's own docstring says, immediately before the assertions, "so metrics are
NOT identical there; the assertions below pin that divergence rather than
skip it" (`:3138-3139`) — a claim that the assertions comprehensively pin
*the* divergence — and two paragraphs later distinguishes "fold metrics
(economic_seq, working, pending)" as the three things "match" when nothing is
pending (`:3140-3141`), explicitly naming `working`/`pending` as first-class
members of "fold metrics" elsewhere in the same docstring. A reader is told
economic_seq is what diverges and that the divergence is pinned; two of the
four `_fold_metrics()` fields silently diverge by exactly the amount the fold
mechanics guarantee (1→0/0→1, every time, for a reason internal to
`_fold_order_event` that has nothing to do with Recovery specifically) and are
neither asserted nor named as a further, accepted gap.

This matters against this lens's own bar because plan §6 Phase 5 item 3's
literal requirement is "positions, cash, NAV, **and metrics**" identical at
every boundary — not "economic_seq" specifically. At the one boundary this
test itself flags as the interesting, non-trivial case (a pending intent),
three quarters of the "metrics" fields checked in the non-pending branch
(`n_working`, `n_pending`, plus the correctly-pinned `economic_seq`) reduce to
one field asserted and two left completely uncharacterized. This is the
"weaker claim than advertised" pattern named in the review brief as at least
a Major: the docstring's own language ("pin that divergence", naming
`working`/`pending` as fold metrics) invites the reader to believe the
pending-boundary case is now fully characterized, when it is only
one-third characterized (of the three non-`n_positions` fold-metric fields).

A conforming fix would either assert `got["metrics"]["n_working"] ==
expected["metrics"]["n_working"] + len(pending_before)` and the symmetric
`n_pending` line (since the divergence is deterministic and reproducible, as
shown above), or narrow the docstring's own claim to say explicitly that only
`economic_seq` is pinned and `n_working`/`n_pending` are known to diverge but
left unpinned. Neither is done.

## Items not found defective

Re-verified by direct reproduction, not just reading, since a prior cycle's
own docstring turned out to misdescribe its test:

- **Item 6, positions/balances/NAV, and the `economic_seq` half of metrics
  (cycle 2's Major, the false-identity claim):** genuinely closed. I
  reproduced all 13 named boundaries: `positions`/`balances`/`nav` match the
  uninterrupted fold at every boundary (unconditionally asserted,
  `test_loop.py:3161-3163`), and `economic_seq` at the three pending-intent
  boundaries now shows the real `+1` divergence the assertion expects,
  falsifiable (confirmed: patching `Recovery._query` to a no-op makes the
  assertion fail). The scenario's `tick` record now closes right after
  `decision` (`:3049`), so `_close_ticks`'s tick-closing side effect
  (`_fold_tick`/`_fold_decision`, which touch only `_open_ticks`/`_history`/
  `_tick_plans` — verified by reading `state.py:1099-1127`, never
  positions/working/pending/economic_seq) is exercised harmlessly at the two
  earliest boundaries (`cash_flow`, `decision`, where the tick is genuinely
  still open) and is a no-op at every later boundary — I reproduced both: the
  `else` branch's full-dict `got["metrics"] == expected["metrics"]` holds at
  all 10 non-pending boundaries, including the two where `open_ticks_before`
  is truthy, closing cycle 2's `open_ticks_before`-gating complaint for
  `n_working`/`n_pending` outside the pending-intent case.
- **The dead `(kind == "fill" and rid == "fill-close")` OR clause**
  (cycle 2's Nit): removed; `named_indexes` now reads `if rid != "snap-empty"
  and (kind in _NAMED_BOUNDARIES or kind == "_real_snapshot")`
  (`test_loop.py:3144-3145`), confirmed by diff. `saw_exit` is now tracked
  separately (`rid == "fill-close"`, `:3155-3156`) and asserted required
  (`:3183`); I reproduced both `saw_pending_intent` and `saw_exit` as `True`
  for this scenario.
- **`report.queried_refs == ()` on the full (no-crash-equivalent) scenario**
  (new in this commit, `:3196`): genuinely checked — reproduced `queried=()`
  when nothing is pending at the end of the scenario.
- **Items 1–5** (no-lookahead over the real `ReplayFeed`/`ReplayClock`, the
  integrated `ServeLoop`+`ReplayFeed`+`ReplayClock`+`PaperExecutor`+ledger
  run, order/fill fidelity, rejection/partial-fill, forced exit): unchanged
  by this commit (`git diff eb61aa0..b25159f -- tests/production/
  test_executor.py tests/production/test_feed.py tests/production/
  test_clock.py` is empty) and were re-verified with direct reproduction in
  the cycle-2 review with no residual findings. I re-ran the full suite
  (775 passed) and spot-read `test_a_tick_over_replay_feed_and_replay_clock_
  keeps_later_tape_unconsumed` and `test_serve_loop_with_replay_feed_clock_
  paper_executor_and_ledger_is_deterministic`
  (`test_loop.py:3201-3407`) to confirm they still construct the real
  `ReplayFeed`/`ReplayClock`/`PaperExecutor`/`JsonlLedger` together — nothing
  in this commit touches them.

## Disposition

Zero Critical, one Major. Cycle 2's specific finding (the false `economic_seq`
identity claim at a pending-intent boundary) is genuinely closed with a real,
falsifiable, reproduced assertion. But the same fix, in closing that one
field, surfaces — without asserting or disclosing — a further divergence in
two more fold-metric fields (`n_working`, `n_pending`) at the identical
boundary the test itself calls out as the interesting case, which the plan
names as required ("metrics") and the test's own docstring implies is fully
pinned ("the assertions below pin that divergence"). Per this lens's bar
(zero Critical, zero Major to pass), this still fails.

**Verbatim verdict:** `FAIL — 0 Critical, 1 Major, 0 Nit.`
