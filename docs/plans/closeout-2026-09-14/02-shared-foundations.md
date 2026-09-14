# 02 — Close shared chronology, input, profile and accounting slices

These are four bounded packets, not a new lane. Follow the [index](README.md)
and master's controlling manifest. F3/F5b/E1 remain forecast-capital-owned;
C0 remains replay-ops-owned. Use a separate reviewed branch per completed slice.

## F3: causal source roster and event order

**Requires F1/F2/F4/F5a.** Do not start while whole F5a is open. Read master F3
and reuse `production/feed.py`, `bundles.py` and ReplayClock.

Freeze roster scope, provenance, zero-event sources and rank derivation before
data/tape output. Synthetic dual G1+G2 authority must be required; G2 alone
refuses before data/member/capture access. Ranks derive from the complete
authorized source universe, not the subset that happened to emit events.
Preserve PUBLISHED-before-consumer-document and later F5a capture chronology.

RED families: absent/wrong/expired authority; missing/duplicate/noncanonical
source identity/rank; unknown and zero-event sources; equal-time ties; reordered
availability; roster/policy/descriptor substitution; deterministic restart.
Use `tests/production/test_feed.py::test_roster_rejects_g2_without_g1` plus
the full focused feed/bundle matrix. GREEN extends existing feed/tape seams,
not another scheduler. Exit requires stable canonical ordering and rejection
before effects for every invalid authority/identity case.

## F5b: captured consumer injection

**Requires F3/F5a.** Scope stays the master's generic captured-binding seam in
`dskit/production/bundles.py` and thin child forecast_bundle adapter/tests.

Inventory current CapturedBindings ownership before editing. Require the exact
V2 verified set and inject a nonserializable binding only at the declared input.
Test wrong descriptor/port/receipt/release, copied or foreign-session bindings,
and a valid declared-port consumer. Use
`children/intraday_equities/tests/test_forecast_bundle.py::test_forecast_consumer_requires_v2_captured_set`.
No ambient paths or later reconstruction may replace trusted captured input.
Two final independent lenses apply despite any older “sole review” prose.
This packet does not implement calibration/statistical policy.

## E1: captured execution profile

**Requires F1/F2/F3/F4.** This can follow F3 without waiting for the whole
forecast/calibration chain. Keep the forecast-capital owner and serialize
changes to feed.py with F3.

Enumerate actual supported profile knobs: sessions/calendar/halts/auctions,
price/FX/borrow/margin/financing, fees, tick/lot/order/cancel constraints,
corporate actions, and valuation. Existing child policy remains child-owned;
generic capture/validation stays generic. Do not claim unimplemented queue,
hidden-liquidity, impact or venue realism.

RED covers missing required data, unknown options, profile/release hash swaps,
tie/order changes, and valid captured profiles. Sentinel:
`tests/production/test_feed.py::test_e1_tie_swap_refuses`.
Test each declared option at its owning adapter; no live/market execution.
Exit: no order, valuation or simulated fill proceeds with a missing,
unsupported or mismatched captured profile.

## C0: one ledger-derived account state

**Requires F1/F2/F4.** Dependency-ready at the planning baseline. Read master C0,
current records/state/ledger/accounting/cashflows/report code and package docs;
do not confuse a historical TODO claiming “no returns producer” with inventory.

SeriesState.apply remains the single persistent fold of ChainLedger records.
AccountState/snapshot/report utilities consume that result; accounting.py must
not introduce a second persistent state machine or returns simulator.

Start with
`tests/production/test_state.py::test_c0_nonancestor_account_refuses`.
Then cover balanced postings; genesis/ancestor identity; initial flow versus
scheduled V1/V2 contributions/withdrawals; corrections/busts; stale price/FX;
splits/dividends; NAV/TWR/MWR snapshot identities; uninterrupted versus restart.
Use synthetic ledgers and independent small worked-account examples.
Run focused state tests plus directly affected ledger/cashflow/report tests.
Exit: nonancestor/stale/unbalanced evidence refuses before mutation, valid
fold/restart identities agree, and no second ledger/accounting owner is added.

## Assignment and closure

For each packet, capture its own prerequisites, allowed paths and source pins,
run high-risk Phase 0 as required, then focused TDD and two final lenses.
A C0 exit does not close R1; an E1 exit does not close C1. Do not reuse these
tests as evidence that the external production authority or real-data gates
have been installed. Each packet appends its own existing-schema review chain.

## Finish: review, merge, push, purge, wrap

For each completed bounded packet, apply the [shared closeout procedure](README.md#mandatory-closeout).
Retain two clean independent lenses with zero unresolved in-scope Critical/Major,
focused evidence and visible minor dispositions. Update RE-ENTRY and the packet
record, merge the reviewed candidate with current main, push and verify remote
containment, then purge only the completed task branch using an expected-head
check; finish with wrap. A partial packet does not close its parent feature.
Keep an old source branch until its entire payload is contained in main or its
unmerged remainder has an explicit approved disposition. If blocked, preserve the
branch and evidence, do not merge or purge it, and wrap with the exact next gate.
