# 01 — Close F5a capture and admission

**Owner:** forecast-capital. **Requires:** verified F1/F2/F4 development exits.
**Current state:** PR 14 source `c489199` has a stopped consume-once Major and
a revised, not-yet-clean driver-only design. No whole-F5a merge is authorized
by its old partial ReviewExit files. Apply the [index](README.md) first.

## Read and preserve

Read the master's F5a/ADR-0125 chronology and controlling manifest, main's
accepted F4 ADR-0126, and these source files, first at `c489199` and on main
at the same paths since 2026-09-26 (record 0045; the branch is retired):
`docs/review-evidence/F5a/0027-review-verdict.v1.json` through
`0032-phase0-driver-only.v1.json`. In particular, owner decisions 0028/0029
stop consume-once Review24 and direct the remaining clusters. This planning
request does not silently rescind that stop.

F4 GREEN `e4c3a2a` and its `dskit/pipeline/trust.py` boundary remain frozen.
Start a current-main task worktree; inspect the source diff and carry only the
selected packet's necessary code/evidence. Do not restart the 23-round loop.

### Approved Packet 4 ownership correction (2026-09-14)

The F4/core lifecycle authority owner approves one narrowly versioned public
broker seam for Packet 4. It may evolve `dskit/pipeline/trust.py` only to keep
the existing authority as the sole writer while ordering consumed admission ->
per-PCE `CapturedPortAuthorization.v2` -> exact
`LifecycleCapturedReceipt.v2` -> complete verified
`CapturedAuthorizationSet.v2` ->, for replay, exact
`ReplayCaptureAdmissionEvidence.v1` -> distinct bound `LaunchSession`.

This correction does not authorize a parallel writer, caller-supplied receipt,
session-before-set/evidence route, existing v1 behavior break, parser/CLI/compose
capture route, durable/restart consume-once claim, real activity, or deployment.
`F5A-R23-ctor-intern-unspend` remains open. Before RED, a successor Phase 0
matrix and fresh skeptic must approve the exact public methods, opaque states,
failure atomicity, signature ownership, and compatibility tests.

## Ordered packets

1. **Resolve the driver-only design.** Independently review matrix 0032 against
   the failed 0031 verdict. The proposed facade accepts exactly the intended
   HistoricalStudyVerifier and delegates only through its capture method; it
   must not become a second writer or retain a bypass broker. Test constructor
   aliases, subclasses/ducks/wrappers, held-object substitution, retry after
   refusal and copied facade state as a family. Explicitly state that F4's
   test broker remains callable: a facade alone does not make it globally
   inaccessible. Resolve design findings before RED. A material authority
   change needs owner approval.
2. **Driver-only implementation.** After that design is clean, implement only
   the approved facade in `dskit/production/verifier.py` and its focused
   `tests/production/test_capture_lifecycle.py` tests. Preserve the existing
   valid bind/capture path and F4 tests. No pipeline-to-production import,
   CLI route, broker monkeypatch or change to frozen trust.py. A clean bounded
   facade can merge separately, but leaves whole F5a and consume-once open.
3. **Unsigned schemas/signatures.** Freeze the exact ADR-0125 object set, trusted
   signer/clock/revocation inputs and chronology. Prove missing/extra keys,
   unsigned or altered payload, wrong authority/purpose, expiry/revocation and
   predecessor substitution refuse before capture/session effects. Synthetic
   authority stays explicitly nondeployment. Implement no permissive placeholder.
4. **CapturedAuthorizationSet.v2.** Prove canonical ordering, exact descriptor/
   port/receipt/runtime/release equality, self-digest, duplicate/missing entries,
   post-freeze mutation and same-session misuse. The valid publication → frozen
   consumer → authorization → captured receipt → distinct session path must work.
5. **Captured-artifact grammar.** Preserve ordinary JSON/hash compatibility and
   restrict descriptors to legal complete node-input positions. Cover every
   public parse/plan/CLI route, forbidden stages/carry/model-load spellings,
   aliases/nesting and mutation before import/open/output. Coordinate any
   pipeline parser change through its approved manifest owner; do not broaden
   the current narrow verifier packet without an approved ownership correction.
6. **Descriptor/receipt equality and restart linkage.** Sweep equal-looking but
   differently bound captures, producer/root/member/receipt/session substitutions,
   partial failure/retry, wrong process/run/purpose and stale restart identity.
   One authorization must not silently become a second consumption authority.
7. **Replay-tape descriptors.** Require exactly the approved tape_manifest and
   tape_data captured inputs and their outer/parent/policy/count/order bindings.
   Test swaps, missing/extra/nested descriptors and admission-before-root creation.
8. **Consume-once authority decision and closure.** Keep
   `F5A-R23-ctor-intern-unspend` visible throughout. Prepare a concrete decision
   explaining which process controls mutable state and who owns durable admission.
   Obtain an explicit owner decision before resuming the stopped correction or
   changing its threat model. Do not rotate through more Python identity containers.
   Then implement/test the approved solution, including concurrent calls,
   write-then-raise, clone/replay and restart behavior within that actual boundary.

Packets 3–7 require their own proportional contract matrix and any missing
design/ownership approval. Existing plans authorize some generic seams but do not
make every newly proposed file or security design accepted. Keep generic mechanisms
in their approved core/pack owner and equity policy in the child.

## Evidence and feature exit

Use the manifest sentinel
`tests/production/test_capture_lifecycle.py::test_private_plan_precedes_capture`,
plus tests for the selected packet's full matrix and directly affected
`tests/pipeline/test_trust.py` cases. A single sentinel pass is not the matrix.
Read current test inventory before adding duplicates. Do not run real capture.

Whole-F5a closes only when all required clusters and the stopped Major have a
valid resolved disposition under approved authority, all current dependencies
are pinned, and two fresh final lenses find zero unresolved Critical/Major.
Append successor evidence without rewriting older JSON. Only that whole-slice
exit unblocks F3/F5b. Never merge all of PR 14 because one new facade is clean.

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
