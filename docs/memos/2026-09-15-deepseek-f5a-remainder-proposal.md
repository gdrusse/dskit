# Proposal — DeepSeek completion of F5a integration and implementation

**Status:** draft for owner approval. **Prepared:** 2026-09-15.
**Starting point:** `origin/main@1fc290f6f2a8cd1d1944994d351f608f00ca1734`.
**Preserved source:**
`origin/cursor/r5-f5a-private-plan-0f39@c48919944588a2b29ba047e4cb551050a1ff9868`.

## Executive proposal

Assign **DeepSeek v4 Pro** (`opencode-go/deepseek-v4-pro`) as the single primary
implementer for the remainder of F5a. It should complete Packets 5–8
sequentially under the repository's owner-adopted implementation framework,
then produce the whole-F5a integration candidate. Use one agent at a time and
fresh independent reviewers only at defined gates.

The main acceleration is to prepare one shared **F5a remainder contract pack**
before Packet 5 RED. That pack inventories the common public entry points,
authority actors, identities, transitions, failure families, owned paths, and
dependencies once. Each packet then adds only a small delta matrix. This keeps
the new framework while avoiding four separate rediscovery cycles.

This assignment does not authorize F3/F5b, real data, replay execution,
training, HPO/refit, a full backtest, paper/live activity, deployment, or
discarding the preserved source branch.

## Why Packet 4 took so long

Packet 4 was not primarily delayed by implementation or test execution. Its
focused 1,443-test run took about 67 seconds. Most elapsed time was spent
settling a high-risk authority contract before code:

- the design crossed signature, lifecycle, concurrency, failure-atomicity,
  session, and sole-writer boundaries;
- eight successor matrices preceded the accepted Matrix v9;
- reviewers found family-level omissions whose fixes changed the contract and
  therefore invalidated earlier design approval;
- material candidate changes correctly reset both final review lenses;
- final delivery required a post-main topology and blob-lock review.

That rigor prevented an unsafe parallel writer and a false restart-safety
claim, but the work repeatedly re-inventoried the same boundary. The remainder
plan below retains the safety gates while reducing that repetition.

## Objective and exit

Deliver a current-main whole-F5a candidate that:

1. implements and separately closes Packets 5, 6, 7, and 8;
2. resolves `F5A-R23-ctor-intern-unspend` through an explicit owner-approved
   durable authority boundary, not another Python identity container;
3. preserves the accepted Packets 2–4 behavior and Packet 4 authority;
4. passes the controlling manifest sentinel and affected focused tests;
5. receives two clean fresh final lenses on one immutable whole-F5a candidate;
6. is integrated, pushed, remotely verified, and wrapped without deleting the
   preserved source until every remainder has an approved disposition.

Only that exit may unblock F3/F5b. Development closure remains
`deployment_eligible=false`.

## Operating model

- **Primary implementer:** DeepSeek v4 Pro, one continuous context when
  practical, with a written handoff after every packet.
- **Coordinator/owner gate:** confirms cross-owner paths, ADR changes, and the
  Packet 8 durable authority decision.
- **Phase 0 skeptic:** one fresh independent reviewer for each high-risk delta.
- **Final reviewers:** two fresh independent reviewers, sequentially, on the
  same immutable candidate: correctness/authority, then tests/integration.
- **Concurrency:** one implementation or review agent at a time. No concurrent
  edits to shared files.
- **Correction rule:** batch all findings from a review by defect family. After
  three failed correction cycles, or earlier repetition of the same boundary,
  perform the mandatory convergence checkpoint before another patch.
- **Review economy:** do not run extra scans after both final lenses are clean;
  defer Nits after lock; evidence-only appends do not restart unchanged code.

## Gate 0 — current-main inventory and shared contract pack

Before RED, DeepSeek creates one evidence-backed inventory from current
`origin/main`, the controlling plan/manifest, ADR-0125/0126, Packet 4 Matrix v9
and exit, and the preserved source pin. It must:

- enumerate every relevant public parse, plan, CLI, verifier, lifecycle,
  facade, and replay-descriptor entry point;
- map actor, authority, trusted/untrusted input, identity, transition,
  expected result, forbidden effect, and focused test for each invariant;
- inventory existing tests before declaring a manifest test `NEW`;
- identify exact path ownership and request one decision for cross-owner edits;
- freeze the compatibility baseline for accepted v1 behavior and Packets 2–4;
- separate Packet 6 restart-identity refusal from Packet 8 durable/restart
  consume-once authority so Packet 6 cannot overclaim durability;
- retain `F5A-R23` as open until the Packet 8 decision and GREEN proof.

Review the shared pack once for omissions and internal consistency. A packet's
delta matrix cites it and describes only new or changed invariants. A material
shared-contract change still requires a fresh Phase 0 review.

## Packet sequence

### Packet 5 — captured-artifact grammar

Freeze the exact legal `$captured_artifact` descriptor and its only legal
location: the complete value of a declared node input. Inventory and test all
public parse/plan/CLI spellings and aliases before editing. Refuse forbidden
stages, carry/`$prev`, params, outputs, defaults, lists/maps/nesting,
artifact/read/path/model-load poison, and mutation before planning, provider or
filesystem access, broker construction, node import, or output creation.

Obtain ownership approval first for any parser/planner/CLI path outside the
forecast-capital lane. Do not introduce a second planner or place capture
authority in JSON.

**Exit:** exact positive grammar plus the inventoried negative family,
ordinary JSON/hash compatibility, focused RED→GREEN, compatibility tests,
clean final lenses, merge/push/remote verification, and packet wrap.

### Packet 6 — descriptor/receipt equality and restart linkage

Bind descriptor, derived port, published root/receipt, producer, member
manifest, captured receipt, authorization entry, run, process, purpose, and
session identities. Sweep equal-looking substitutions, partial failure/retry,
wrong process/run/purpose, stale restart identity, and equivalent entry points.

Packet 6 proves equality and fail-closed restart identity only. It must not
claim durable consume-once or reconstruction; those remain Packet 8.

**Exit:** substitutions refuse before member access/session/effects; accepted
Packet 4 and v1 paths remain green; clean final lenses and packet wrap.

### Packet 7 — replay-tape descriptors

Require exactly the approved top-level `tape_manifest` and `tape_data`
descriptors with outer/parent/policy/count/order bindings. Test missing, extra,
duplicate, aliased, nested, swapped, and equal-port/different-capture families
before planning or root creation. Reuse existing bundle/capture authorities;
do not create a parallel tape engine or run a market replay.

If Packet 7 depends on an unavailable F3-owned producer identity, implement
only the locally decidable grammar/binding slice and record the exact dependency
instead of fabricating a placeholder or claiming the whole packet.

**Exit:** synthetic descriptor/binding behavior is closed to its approved
scope, dependencies are explicit, prior packets remain green, clean final
lenses and packet wrap.

### Packet 8 — durable consume-once authority and `F5A-R23`

Begin with an owner decision, not code. It must name the process/service that
owns durable mutable state, transaction and locking semantics, crash/restart
recovery, idempotency key, unknown-outcome resolver, and authority for admission
consumption and unspend refusal. Mutable object identity, constructor aliases,
intern tables, or process-local locks are not a durable security boundary.

After approval, use focused RED→GREEN for concurrent calls, write-then-raise,
clone/replay, process restart, second-process contention, partial persistence,
recovery, and exact consume-once outcome. Reuse Packet 4's transaction shape
and one authoritative ledger; do not create a second writer.

**Exit:** the owner-approved durability boundary is implemented and proven,
`F5A-R23` has a truthful disposition, no bypass exists, clean final lenses and
packet wrap. If the owner refuses or the durable service is unavailable, stop
with a precise blocker; do not simulate closure in memory.

## Whole-F5a integration gate

After all four packet exits are contained in current main:

1. build one immutable whole-F5a candidate from the accepted commits;
2. verify
   `tests/production/test_capture_lifecycle.py::test_private_plan_precedes_capture`;
3. run the union of affected focused suites and compatibility interfaces using
   `/home/russell/dskit/.venv/bin/python`, worktree cwd, and `PYTHONPATH=$PWD`;
4. run Ruff and `git diff --check`; use the full suite only if touched code or a
   new failure justifies it;
5. run two fresh final lenses on that exact candidate and retain all findings;
6. update the evidence chain and `docs/RE-ENTRY.md` without rewriting history;
7. integrate current main, verify affected interfaces and reviewed identities,
   push without force, verify remote containment, safely purge only contained
   clean task branches, and wrap.

## Time-control checkpoints

These are stop/reassess budgets, not delivery promises:

- shared inventory/contract pack: one focused planning block;
- each Packet 5–7 design: one author pass plus one skeptic pass before RED;
- each correction round: one batched family correction, not issue-by-issue
  reviewer ping-pong;
- Packet 8: stop immediately if the durable owner/technology decision is absent;
- final reviews: exactly two after candidate lock unless a material defect
  reopens it.

At each checkpoint, report elapsed time by category: inventory/design,
implementation, tests, review, correction, and integration. This makes delay
visible and prevents review count from being mistaken for coding time.

## Copy-ready DeepSeek assignment

> Work autonomously as the single primary implementer for DSKIT F5a Packets
> 5–8, starting from current `origin/main` and preserving
> `origin/cursor/r5-f5a-private-plan-0f39@c48919944588a2b29ba047e4cb551050a1ff9868`.
> Follow `docs/skills/implementation-workflow.md`,
> `test-drive-development.md`, `skeptic-review.md`, and `wrap.md`. First prepare
> one shared F5a remainder contract pack covering public entry points, owners,
> actors, authorities, identities, transitions, failure families, tests,
> forbidden effects, and non-goals. Then complete Packets 5, 6, 7, and 8
> sequentially with thin delta matrices, Phase 0 where required, meaningful
> focused RED→GREEN, compatibility checks, immutable candidates, and two fresh
> sequential final lenses. Batch findings by defect family and use the mandatory
> convergence checkpoint after three failed cycles or an earlier repeated
> boundary failure. Obtain owner approval before cross-owner parser/CLI edits
> and before Packet 8's durable consume-once design. Never substitute
> process-local identity for durability, create a second writer/planner/ledger/
> tape engine, bulk-merge the source branch, or overclaim a partial packet. Use
> WSL2, `/home/russell/dskit/.venv/bin/python`, worktree cwd, and
> `PYTHONPATH=$PWD`. Use synthetic fixtures only. Do not run real data/replay,
> training, HPO/refit, a full backtest, paper/live activity, deployment,
> environment changes, or a full suite unless directly justified. Merge, push,
> purge, and wrap each clean bounded packet. Stop with the exact blocker if an
> owner decision or dependency is missing. Only after all four packet exits and
> a clean whole-F5a integration review may F5a be called closed or F3/F5b be
> called unblocked.

## Owner decisions requested

Approval should explicitly confirm:

1. DeepSeek v4 Pro as primary implementer for Packets 5–8;
2. one-agent-at-a-time work with fresh sequential independent reviewers;
3. permission for the shared remainder contract pack and thin packet deltas;
4. the owner route for Packet 5 cross-lane parser/CLI changes;
5. the durable state owner and permitted persistence boundary for Packet 8;
6. whether clean packets merge individually (recommended) or wait for one
   combined whole-F5a merge.
