# Quality fixes and legacy branch disposition

These are separate bounded maintenance packets. They must not delay a locked
feature for cosmetic reasons or become a new repository-wide review loop.

## Q1 — use the prepared Claude trial

Read [the trial handoff](../../handoffs/2026-09-14-claude-workflow-trial.md).
Prepared branch: `trial/claude-registry-isolation-20260914`; worktree:
`/home/russell/wt/claude-workflow-trial-20260914`. Check whether its owner has
started or completed it; do not create a duplicate or edit that worktree.

The packet fixes the test-registry order dependency exposed by `synth-source`.
Retain a minimal same-process RED, real adapter registration, full shipped-kind
negative coverage, both execution orders and reuse/module-cache behavior.
Use two fresh Claude review lenses as that explicit task requests. Merge/push/
purge/wrap only its completed scope. It closes a test defect, not F5a or runtime
readiness. Record the observed result here or in the existing handoff at closeout.

## Q2 — F2's deferred lint nits

Re-inventory the current tree for D202 in pipeline `__main__.py` and F401 in
`stages.py`; audit locations were lines 249 and 16, which may move.
Separate purely editorial spacing from removing an import with possible import
side effects. Fix only still-present findings; prove behavior/import compatibility
for a semantic removal and use the shared final review gate.
Run focused Ruff on changed paths; no full suite or unrelated style sweep.
Do not reopen the accepted F2 contract merely to fix a nit. If already corrected,
record the containing commit and close without a duplicate change.

## Q3 — remaining docstring ignore inventory

The audit's TODO reported 30 remaining modules; derive the current count from
`pyproject.toml` and file contents before selecting work. Previous closure
covered 24 files; do not repeat its 29-round review methodology.

Choose a small related file family. Write accurate public input/output/exception
contracts from the implementation, including concrete false promises found by
callers. Remove only those files' justified Ruff ignore entries. Check syntax,
focused doc lint and AST equivalence with docstrings removed. An executable
change is a separate behavior packet with targeted tests and final review.
Ordinary missing cross-references are Minor/Nit unless a demonstrated material
contract failure warrants more. Stop at the locked acceptance result.

Exit: selected files meet the existing documentation standard, the removed
ignores are earned, and the remaining count is updated. A partial batch does
not mark all docstrings done.

## Q4 — reconcile unique local histories

Fetch and inspect current refs before disposition. Audit source tips were:

- `codex/f1-evidence-reference-correction` at `ff19c2e` and
  `codex/f1-review-evidence-terra` at `c87dcf0`: reported patch-equivalent
  successors; prove equivalence and preserve evidence before deleting anything.
- `docs/final-model-replay-adr` at `406aab1`: 22 unique commits ending in a
  round-19 correction, not a clean final pass. Inventory contract/code/evidence
  against plans 03/05 and main; extract only reviewed missing scope.
- `local-wrap-2026-09-07` at `6df051b`: inventory unique payload and route each
  still-needed change to its actual owner; no bulk merge on the branch name.
- `wrap-tft-rf-runs` at `c080cb6`: shared `/home/russell/dskit` had modified
  `opencode.json` and two untracked child model-zoo JSONs. Preserve the checkout
  and dirty work; obtain its owner's disposition before touching them.
- Local clustering may lag its remote (`5bf87ad` versus `a450d2a` at audit).
  Reconcile both tips in plan 07; a remote merge alone is not proof of local
  containment. Replay source is handled in plan 05.

Use a retained inventory of unique commits, patch-equivalent content, required
evidence and remaining owner decisions. Review a selected missing delta on
current main; do not cherry-pick older contracts over accepted newer contracts.
Git ancestry is the normal purge proof. Patch-equivalence without ancestry
requires explicit approved disposition of the unmerged remainder. Do not delete
dirty worktrees or rewrite immutable review hash chains.

Exit: each history is either safely contained and purged or explicitly retained
with an owner/next action. A retained branch is not falsely labeled completed.

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
