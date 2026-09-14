# Skeptic review and convergence

Read implementation-workflow.md for automatic scope, authority, and delivery
routing. This is the owner-adopted review procedure as of 2026-09-14.

## Scope and severity

Use independent review before declaring production-bound code ready to merge or
deploy, and for high-risk design before implementation. Pure editorial changes
need proportionate accuracy/link checks; policy or behavioral contract changes
need independent review. Local checkpoint commits may precede approval and must
not be described as ready. Deployment retains its separate authority gates.

Reviewers assign severity from a stated contract and demonstrated consequence:

- **Critical:** severe authority breach, irreversible data loss, or comparably
  severe incorrect operation.
- **Major:** material wrong results, accounting/replay corruption, important
  compatibility failure, missing essential protection, or a test defect that
  hides one of these under the approved contract.
- **Minor:** limited impact with no demonstrated material contract violation;
  ordinary documentation omissions or small coverage gaps can be deferred.
- **Nit:** editorial, formatting, or preference-only improvement.

For design findings, demonstrate the concrete incorrect implementation the
contract permits and its impact; a preferred wording or longer enumeration is
not automatically Major. A false documentation promise can be Major when its
use causes a material failure. Do not automatically promote every exception
cross-reference or speculative edge case.

Each finding states severity, invariant, location/entry point, reproducer or
worked counterexample, consequence, defect family, and disposition. The author
cannot lower severity or narrow scope to escape a gate. A disputed finding goes
to a fresh independent adjudicator. Newly proven material defects remain
eligible even if the original matrix missed them; record and evaluate them.
Owner approval is required for a material contract/threat-model change.

## Phase 0: high-risk design

Required for changes crossing trust boundaries, identity/persistent-state
contracts, coordinated public entry points/lifecycle stages, or irreversible
effects. Inventory existing seams; freeze actors, authority, trusted/untrusted
inputs, identities, state transitions, compatibility, and non-goals. Map each
invariant to input families, entry points, expected outcome and forbidden effect.

Have an independent design skeptic challenge that contract and inventory before
RED. Give it room to find omitted attacks, not a prompt seeking approval.
Resolve all Critical/Major findings and obtain required owner/ADR approval.
The matrix must include relevant alias/subclass/mutation, expiry/revocation,
identity/default/path substitution, partial failure/retry/crash/concurrency,
pre-validation effects, and every public facade. Do not invent irrelevant
attacker powers or present in-process state as an external authority.

## Candidate round

1. Freeze an immutable candidate commit plus contract/matrix/dependency
   identities. Dispatch at least two fresh independent skeptics with distinct
   lenses: correctness/authority and tests/integration. Sequential is the
   default; explicit task instructions may choose another mode/model. Neither
   reviewer is the implementer or the other reviewer.
2. Each skeptic examines its complete assigned matrix and equivalent entry
   points, attempts to falsify invariants, and returns all proven findings
   together. Record uncovered matrix rows rather than claiming exhaustiveness.
   Early return is allowed only when unsafe or blocked, and is not a clean pass.
3. Run both lenses on the same candidate before batching corrections. If the
   first uncovers a boundary-invalidating defect, stop that candidate, retain
   the result, and return to Phase 0 instead of reviewing an invalid design.
4. Correct material findings with regression tests, sweep the whole affected
   family, then have fresh contexts review the changed candidate. Changes to
   code, tests, behavioral contracts, matrix semantics, or relevant dependencies
   reset both final lenses; do not carry a clean pass across those changes.
5. Pure editorial corrections or appending evidence do not reset unchanged
   approvals. Verify unchanged reviewed blobs and contract/matrix/dependency
   identity, and record the comparison. If an edit changes an invariant, gate,
   ownership, allowed input, or expected behavior, it is not editorial.
6. Two completed independent lenses reporting zero unresolved in-scope
   Critical/Major, relevant tests, required checks and a recorded Minor/Nit
   backlog close the candidate. No extra hunt follows just because closure was
   reached. Defer nits after the lock; changing code to fix one starts a new
   candidate. Never describe scoped development approval as deployment safety.
7. A new proven Critical/Major after closure reopens the affected candidate;
   retain the old evidence and record why it is superseded. An earlier partial
   ReviewExit never overrides a later open finding.

If an existing evidence schema requires additional fields/counts, preserve them.
Do not rewrite old verdicts, hash chains, or policy history to match this policy.
Do not emit a ReviewExit that conceals unresolved whole-slice findings.

## Convergence checkpoint

A failed correction cycle is correction followed by a fresh review that still
finds Critical/Major. After three consecutive failed cycles, no fourth patch
until an independent checkpoint. Repeated families, widening scope, or a broken
authority boundary trigger it earlier. Count across handoffs; do not reset the
counter by renaming a task or recording “localized; continue.”

The checkpoint must produce a concrete changed approach: full sibling/entry
inventory and batch correction, smaller separately testable slice, seam
refactoring, or a revised contract/authority with required approval. List which
earlier fixes failed to cover the family and why the next approach will.
For genuinely distinct localized findings, an independent skeptic may authorize
resuming the same design after recording the complete sweep and its evidence.
Repeated classes cannot use that exception. Preserve existing work; no automatic
rewrite, discarded history, reduced threat model, or waived blocker.

## Retained evidence and reviewer request

Keep actual reviewer output or retrievable transcript/ID, not an author-only
claim that review passed. Record reviewed commit and blobs, contract/matrix,
dependency identities, reviewer/model/lens, commands/results, all findings and
dispositions, correction commit, and gate status in the existing record.

A review request must name the candidate and assigned lens, provide the frozen
scope/matrix and prior family ledger, and ask: “Try to falsify these invariants
across all listed entry points. Return every proven finding with consequence
and severity, tests run, untested limits, and unresolved counts. Do not stop
at the first finding or expand scope for cosmetic preferences.”
