# Wrap DSKIT work

Use implementation-workflow.md and skeptic-review.md. Authorization in the
current task persists: an explicit request to merge/push/wrap when complete
authorizes those steps without another confirmation. It does not authorize
unrelated work, a new architecture, or real execution/deployment.

1. Verify the candidate's latest independent reviews, whole-slice scope,
   zero unresolved in-scope Critical/Major, relevant tests, required checks,
   and disclosed Minor/Nit backlog. Do not trust a stale partial ReviewExit.
   A checkpoint commit is not approval.
2. Freeze code/tests/contracts/dependencies. Update the existing evidence and
   docs/RE-ENTRY.md with reviewed commit, results, limits, remaining blockers,
   and next action. Pure evidence/editorial appends keep approval only after
   checking the reviewed identities are unchanged. Do not fix nits after lock.
3. Commit only this task's changes with the exact model/version author and
   trailer required by the root instructions. If committing is authorized,
   proceed. Never include another session's dirty files.
4. If merging is authorized, fetch origin and integrate from this isolated
   worktree. Preserve both sides of append-only docs/ledgers and follow the
   installed merge drivers. If code, contracts, or relevant dependencies change,
   review that changed candidate and test the affected interfaces before merge.
   A conflict-free merge is not proof of semantic compatibility. Do not force
   main or bypass branch protection/required checks.
5. Push main when authorized and verify the remote contains the accepted
   commit. If genuinely incomplete, do not merge; preserve/push the bounded
   branch only if that is authorized, and record the exact blocker/next step.
6. After a successful merge, delete the task's remote branch only if its current
   head is contained in remote main; use an expected-head check to avoid deleting
   concurrent work. Delete contained local task branches after checking dirty
   worktrees. Preserve unmerged/dirty work and do not remove others' directories.
   General branch cleanup beyond this task needs its own authorization.
7. Verify final remote/PR state. Report what landed, review/check results and
   material limits, what remains, and the next step. Keep chat to about 300
   characters when practical; put substantial evidence in the existing memo.

No extra whole-codebase review after clean closure. A newly proven material
defect or changed reviewed identity reopens the affected candidate; editorial
preferences alone do not.
