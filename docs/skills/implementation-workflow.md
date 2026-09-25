# Implementation and closure workflow

**Default for all DSKIT implementation, review, and wrap work.** Owner adopted
2026-09-14 from [the audit](../memos/2026-09-14-review-progress-and-consolidation.md).
This procedure and skeptic-review.md supersede older process wording about
unbounded rescans, every-defect blocking, and restarting for editorial changes.
They do not override explicit task instructions, approved feature contracts,
dependency gates, evidence schemas, or execution restrictions.

## Start and scope

1. Work in WSL2. Fetch origin, create an isolated worktree from current
   origin/main, and read root/nearest AGENTS.md or CLAUDE.md and RE-ENTRY.
   For a resumed older branch, first read the current origin/main versions of
   this workflow, skeptic-review, and wrap; then reconcile its code and contracts.
   Never pull, reset, or edit another session's dirty checkout.
2. Select one bounded acceptance result. Record its base commit, owner,
   approved contract/ADR, dependencies, allowed paths, explicit non-goals, and
   observable exit criteria in an existing handoff, memo, or review record.
   A user-requested scope/evidence file is authorized; do not create unrelated files.
3. Inventory the existing seam and every equivalent public entry point before
   proposing a new abstraction. Check the master plan's dependency DAG and the
   latest whole-slice findings, not merely an earlier ReviewExit.
4. State actors and authority. Distinguish hostile input from hostile code with
   control of the interpreter. Mutable Python internals are not an external
   security boundary. A narrowed threat model needs owner approval, not a
   unilateral reviewer or author downgrade.
5. Map each invariant to input families, entry points, transitions, expected
   results, forbidden effects, and tests. Split compound conditions into cases;
   include positive compatibility and negative/partial-failure cases. Keep this
   proportional: one small test bug may need only a short inventory.
6. For a significant design, retain ADR-before-code approval. For high-risk work,
   use skeptic Phase 0 before RED. Otherwise proceed to focused TDD.

## Implement and review

Use test-drive-development.md and skeptic-review.md automatically. Fix a defect
family across the inventoried scope in one correction. Do not broaden the
feature or patch only the reviewer's example. Commit an immutable review
candidate; a local checkpoint commit is not release approval.

Use two fresh independent skeptics with distinct correctness/authority and
test-quality/integration lenses, sequentially by default. Require all evidenced
findings for their assigned matrix, their own retained output, and explicit
Critical/Major/Minor/Nit dispositions. Apply the severity and stopping rules in
skeptic-review.md, including the mandatory convergence checkpoint.

Run relevant focused tests and required checks. No full suite unless the touched
code justifies it or the owner requests it. Reproduce unexpected failures on the
unchanged base with the same environment/order before classifying them.
Do not weaken a test or disable a required check to make the branch green.

## Lock and deliver

A lock binds the candidate code/test blobs, behavioral contract, matrix,
dependencies, reviewer identities/verdicts, checks and disclosed minor backlog.
Two clean independent lenses with zero unresolved in-scope Critical/Major and
the required checks close that candidate. If a required external check blocks,
resolve it or seek the authorized owner's decision; never bypass protection.
Do not fix nits after locking. Use wrap.md to integrate authorized work promptly,
verify affected interfaces, push, verify the remote, then delete contained
branches safely. A main update that changes a reviewed dependency invalidates
the affected approval and needs review before landing.

For remaining work, select a bounded packet from the
[closeout plan index](../plans/closeout-2026-09-14/README.md). For the active
historical backtester, preserve the controlling master DAG. Whole-F5a is closed
(F5A-R23's durable consume-once gate landed via ADR-0147, two clean skeptic
lenses, zero unresolved Critical/Major); F3/F5b may now start per their own
stated dependency once their own design work is approved. Reconcile
the replay V2 plan/ADR collision before its implementation. Clustering/RL is
separate and has its own approvals. Development approval never authorizes real
HPO/refit, market replay, lockbox, backtests, paper/live actions, or deployment.

## Evidence that helps the next session

Reuse the current review ledger/schema; do not create a new file for every nit.
Record base/candidate/merge commits, contract/matrix revision, lens/reviewer,
command/interpreter/result, finding family and consequence, new versus
pre-existing defects, correction commit, and disposition. Keep actual reviewer
reports or retrievable transcripts/IDs. Note review time and merge delay when
available; never invent them. Separate runtime fixes, contract fixes,
documentation work, and closed gates. End with the next bounded action.
