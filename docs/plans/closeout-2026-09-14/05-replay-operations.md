# Replay design reconciliation and R1–R5

Owner: replay-ops lane. Main has the F4 ADR-0126; replay source
`97900efd66bfed44cc403c567c04c4e383c05c24` uses ADR-0126 for a different decision.
Start with a documentation packet before implementing the replay protocol.

## D0 — reconcile the actual design delta

Read the pinned branch's decision log and replay plan with `git show`; compare
with current main and the controlling master manifest. The manifest already
contains V2 test targets. Do not replace it wholesale or claim all R1–R3 are V1.

Produce a short mapping of conflicting ADR identity, still-missing V2 rules,
already-integrated text and runtime/schema references. Allocate a collision-free
ADR identity under the repo convention, preserving approval provenance. Changing
signed/runtime identities or protocol semantics requires the corresponding owner
approval; do not rewrite historical append-only evidence or pretend an identifier
rename is semantically harmless. If no protocol delta remains, retain that
evidence and close only reconciliation.

Review the reconciled document packet with two independent lenses and land it
when approved. Keep the old source branch until every unique payload has a
verified disposition. This packet is not R1's ReviewExit or permission to run replay.

## R1 — bootstrap and atomic storage

Dependencies: F2, F3, F4, F5a, C0.
RED: `tests/production/test_replay_state_v2.py::test_r1_bootstrap_reservation_precedes_frozen_plan`.
Own the manifest's planned `production/replay.py:ReplayRun`,
`ledger.py:AtomicJournalReplace`, `state.py:AtomicSeriesStateReplace`
and matching package docs; inventory current implementations first.

Prove bootstrap reservation and initial lease precede plan freeze. Cover rooted
digest paths, lstat owner/mode/link checks, O_NOFOLLOW, locks, same-directory
O_EXCL temporary files, expected-byte/hash/generation rechecks, file fsync,
rename/no-replace genesis and directory fsync. Inject failures at every protocol
cut point with deterministic fixtures; do not assume multi-file replacement is
one atomic transaction. Exit: recovery accepts only the protocol's durable
state and no invalid path/lease can create accepted authority.

## R2 — receipts before projections

Dependencies: R1, C0.
RED: same module `test_r2_receipts_precede_projection`.
Own the R1 surfaces plus the manifest's report seam.

Exercise effects, receipts, ACK, cursor/projection and manifest COMMITTED
ordering. Query before resend; an unknown outcome must refuse, not guess.
Freeze intent before effects and derive results only from receipts.
The signed committed head is the sole visibility boundary. Prove pre-effect
abort only with NO_EFFECT; after activation recovery is non-abandonable except
through the contract's durable authorized handoff.
Exit: every crash/retry fixture has the permitted single effect and projection,
including zero-effect rejection cases.

## R3 — process-route recovery

Dependency: R2.
RED: `tests/production/test_replay_routes_v2.py::test_r3_all_fsync_rename_cutpoints`.
Own manifest loop `ServeLoop._tick_once`, report, CLI `ReplayVerb`
and R1 storage/replay seams.

Drive the real public orchestration/CLI route with bounded synthetic adapters.
Test every fsync/rename checkpoint through restart, not just isolated helper
calls. Bind process restart, leases, receipts, projections and published head.
V1 is inspect-only unless an explicit migration is approved; no implicit V1→V2
upgrade or writable fallback. Exit: uninterrupted and recovered outcomes agree,
and unauthorized routes cannot bypass R1/R2's durability proof.

## R4 — operational authority

Dependencies: R3, C0.
RED: `tests/production/test_loop.py::test_r4_hold_requires_registered_authority`.
Own the manifest's loop/control/cashflows/report paths.

Cover signed holds, expiry/revocation and release pins; record-only cashflows;
returns/monitor ordering; release-calendar rotation, overlaps, gaps and restart.
Use clock-controlled synthetic scenarios and independently expected ledger/
monitor outcomes. Exit: authority and rotation remain correct at boundary times
and after recovery, with no unregistered hold silently taking effect.

## R5 — remove the child bypass

Dependencies: R4, E1, C1.
RED: the manifest's child `test_r5_direct_replay_route_refuses`.
Own child replay/export files plus the production bridge, as specified there.

Inventory direct ServeDocument/ServeLoop construction, legacy exports, registry
entries and DevelopmentReplay routes. Disable or migrate the approved bypasses;
replacement is a normal PipelineDocument with thin child decider/profile/fill/
event mapping through the production bridge. Add no parallel replay engine.
Test every inventoried entry route, positive normal pipeline, legacy refusal,
and identity substitution before effects.
Exit: all supported child replay reaches the one verified bridge; bypasses are
unavailable with a clear compatibility disposition.

## Completion boundary

Run focused synthetic protocol tests only. R5 closure unlocks one I1 dependency;
it does not authorize market replay, lockbox, real backtest or deployment.
Keep R42–R46 from the F4 audit visible in their approved deferred scope; this
plan cannot quietly claim a broader production trust model.

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
