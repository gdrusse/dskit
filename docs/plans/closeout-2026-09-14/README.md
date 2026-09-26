# Remaining feature closeout plans

Baseline: main `3798ffd`, 2026-09-14. These are work directions, not statements
that features are implemented. Re-fetch and recheck branch/evidence status before
starting; another agent may have advanced it.

## Read first and choose one packet

Use [implementation-workflow](../../skills/implementation-workflow.md),
[TDD](../../skills/test-drive-development.md),
[skeptic review](../../skills/skeptic-review.md), and
[wrap](../../skills/wrap.md). Start in WSL2 from a current isolated worktree.
Old checkouts retain old startup hooks; start the agent in a current checkout.
Do not edit another task's dirty checkout.

The [master plan](../2026-09-12-json-pipeline-historical-backtester-tdd.md),
its **controlling executable TDD manifest**, approved ADRs and latest reviewer
findings define feature contracts. These packets organize that work; they do
not change signed identities, file ownership, evidence schemas or architecture.
The new shared workflow controls process. In particular, an older isolated
“sole review” sentence does not replace the two final independent lenses.

The owner's instruction supplies the delivery direction for completed assigned
packets: wrap, merge to main, push and purge the completed branch. It does not
authorize real execution or approve unresolved designs/dependencies. Preparing
these plans does not launch all their work.

## Plan index and coverage

1. [Capture and admission](01-capture-admission.md): F5a and its stopped
   consume-once finding; current PR 14 source.
2. [Shared chronology, inputs, profile and accounting](02-shared-foundations.md):
   F3, F5b, E1 and C0, as four independently assigned packets with their existing
   lane owners. C0 can be selected before F5a closes.
3. [Model release](03-model-release.md): A1–A4.
4. [Forecast and capital](04-forecast-capital.md): B0–B3 and C1.
5. [Replay design and operations](05-replay-operations.md): source-ADR
   reconciliation, then R1–R5.
6. [Integrated acceptance](06-integration-acceptance.md): I1 and I2 verifier
   tests. I2 is not permission to run a real study.
7. [Clustering and RL](07-clustering-rl.md): proposed plan/ADR closure and its
   six implementation/compatibility slices, outside the backtest critical path.
8. [Quality and legacy branches](08-quality-legacy.md): the prepared Claude
   registry trial, F2 minor backlog, docstring remainder, and unmerged local
   history reconciliation.
9. [Other tracked feature backlog](09-other-tracked-features.md): remaining
   open TODO capabilities and explicit deferred design decisions. Re-inventory
   first; historical TODO prose can lag shipped code.

F1, F2 and F4 have retained development ReviewExit records on main; do not rebuild
or reopen them merely because their original branches were purged. Their approved
scope and `deployment_eligible=false` remain material. The F2 guard exit does not
prove that an external production launcher is installed.

## Authoritative development dependencies

This is a transcription of the master DAG at the baseline, for scheduling only.
Compare against the current master before issuing work; a difference requires
reconciliation, not silently changing this graph.

```json
{"F1":[],"F2":["F1"],"F4":["F2"],"F5a":["F1","F2","F4"],
 "F3":["F1","F2","F4","F5a"],"F5b":["F3","F5a"],
 "A1":["F1","F2","F4","F5b"],"A2":["A1"],
 "A3":["A2","F2","F4","F5b"],"A4":["A3"],
 "B0":["F1","F2","F4","F5b"],"B1":["B0"],"B2":["B1"],"B3":["B2"],
 "E1":["F1","F2","F3","F4"],"C0":["F1","F2","F4"],
 "C1":["B3","E1","C0"],"R1":["F2","F3","F4","F5a","C0"],
 "R2":["R1","C0"],"R3":["R2"],"R4":["R3","C0"],
 "R5":["R4","E1","C1"],"I1":["F1","F2","F5a","F5b","A4","C1","R5"],
 "I2":["I1"]}
```

All 21 not-yet-whole-slice-closed nodes have exactly one plan above; the three
existing exits are prerequisites, not new tasks. Do not count this as a
percentage of delivered functionality.

Suggested next selections are the already prepared registry trial, F5a design
checkpoint, replay document reconciliation, or C0's bounded accounting proof.
Do not put multiple agents on shared files. The three master lane owners remain:
model-release (A1–A4, I1/I2), forecast-capital (F3/F5a/F5b/B0–B3/E1/C1), replay-ops
(C0/R1–R5). These documents do not create extra architecture lanes.

## Current branch sources

- F5a: `cursor/r5-f5a-private-plan-0f39`, audited `c489199`.
  Later open findings supersede earlier partial exits.
  Retired 2026-09-26: the branch is deleted; its evidence 0001-0032 is on
  main at `docs/review-evidence/F5a/` (record 0045).
- Replay design: `codex/r5-replay-ops-20260911`, audited `97900ef`.
  Its ADR-0126 is the replay decision; main's ADR-0126 is F4's threat model.
- Clustering/RL: `codex/cluster-rl-framework-plan-20260913`, audited
  `a450d2a`. Its proposed ADR-0122 collides with main's model-release ADR-0122.
- The registry trial has its own prepared local branch/worktree; coordinate with
  its owner and read its result instead of starting a duplicate.
- Local unique/evidence branches are covered by plan 08; never assume a
  deleted remote means every local commit was integrated.

Read unmerged source with `git show <pinned-commit>:<path>`; retain those source
pins when a new worktree starts on main. Do not bulk-merge a source just to get
its plan or tests. A bounded selected delta may be replayed onto main, but
remaining original-branch work must stay discoverable.

## Execution and model boundaries

For master-plan backtester slices, retain its Sol-first, Terra-corrections and
two sequential fresh Terra review policy unless the owner explicitly replaces
it for the selected task. The separate Claude registry trial is an explicit
task-specific override, not a global model change. The clustering source's
GLM/DeepSeek identity/configuration gate remains outstanding. If required models
or tools are unavailable, record the specific blocker rather than inventing
an identity or silently substituting a reviewer.

Use the exact WSL interpreter `/home/russell/dskit/.venv/bin/python`, current
worktree as cwd and `PYTHONPATH=$PWD`; record its actual identity. Old manifest
worktree directories may be detached or stale: adapt cwd to the new isolated
worktree and record the new command, preserving the test node and semantics.
Inventory test names before creating them; NEW in the historical manifest is
not proof a test is still absent. No environment mutation without its approval.

Use synthetic fixtures only for authorized implementation. No real-data source
read, acquisition, market replay, HPO/refit, model training run, lockbox, full
backtest, paper/live action or deployment follows from these plans. Approved
bounded synthetic tests may exercise their explicitly permitted local fixtures.
G0–G7 and all separate owner/security/data/execution gates remain required.

## Mandatory closeout

1. Freeze one packet: owner, base and source pins, exact paths/contract, current
   dependency exits, matrix, success assertions, forbidden effects and non-goals.
   For design-blocked work, complete the design/approval step first; do not
   disguise it as implementation readiness.
2. Inventory once, write meaningful focused RED, implement the smallest generic
   correction, and sweep equivalent entry points/input families. Reuse existing
   seams. Do not introduce a second planner, ledger, returns engine or authority.
3. Test the matrix and affected compatibility interfaces. Reproduce unexpected
   baseline failures with the same command/environment/order. No full suite
   unless directly justified or requested; no blanket skips or weaker assertions.
4. Run two fresh independent final lenses on the same candidate: contract/
   correctness/authority and test-quality/integration. Record actual outputs,
   every finding and consequence, dispositions and exact reviewed identities.
   Zero unresolved in-scope Critical/Major closes the candidate with visible
   minor backlog and required checks. Do not relabel a blocker or narrow a
   threat model without its approval.
5. Batch corrections by defect family. After three failed correction cycles,
   or earlier on a repeated boundary failure, require the independent
   convergence checkpoint and a concrete changed approach before more patches.
   Never reset this counter by renaming a branch. Do not start extra scans or
   edit nits after locking. Material code/test/contract/dependency changes
   invalidate the final lenses; purely editorial/evidence appends do not.
6. Append the appropriate packet memo/journal/review evidence; retain existing
   schema and byte-hash chains. A partial packet may be described as reviewed,
   but must not emit a whole-slice ReviewExit while any slice blocker is open.
   Update RE-ENTRY with exact next dependency, accepted commit and limits.
7. **Merge, push, purge, wrap:** fetch current main, integrate in the isolated
   task worktree, verify both parents' work and affected interfaces, and re-review
   changed approved identities. Push main without force or bypassing required
   checks. Verify the remote contains the accepted commit and any associated PR is merged.
   Only then delete the completed task's remote branch with an expected-head
   check and its contained clean local branch. Preserve dirty work and others'
   worktree directories. End with wrap: outcome, commit, checks, remaining gate.
8. Purge an old source branch only when its whole tip is contained in main, or
   the owner has explicitly approved disposition of every unmerged remainder.
   Review-approved subfeature extraction is not permission to discard its sibling
   work. If blocked, preserve/push the assigned branch as authorized, do not merge
   or purge it, and wrap with a specific blocker rather than a false completion.

## Finish this planning package

Review these directions for dependency/ownership/approval consistency and runnable
handoffs, then merge the completed documentation branch to main, push, verify
containment, purge that completed branch and wrap. This publishes directions;
it does not close any implementation slice or execute its workload.
