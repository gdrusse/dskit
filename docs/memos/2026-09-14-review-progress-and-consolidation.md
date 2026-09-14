# DSKIT progress, review value, and branch consolidation
Date: 2026-09-14. Author: GPT-6.

## TL;DR
Yes, DSKIT is making real progress, but its review process also creates substantial avoidable churn. Keep independent review; replace repeated patch-and-rescan work with explicit boundaries, complete defect-family sweeps, risk-based severity, and a precise stopping rule. Development gate approval is not approval to run a real backtest or deploy.

## Contract and evidence
The owner requested an audit, consolidation of branches whose latest review has zero Critical/Major findings, branch deletion after integration, and a completion plan. That threshold governs this audit; minor documentation/style issues are recorded rather than used to restart closure.

The starting remote main was `36553ae`. I fetched all remote branches, inspected eight non-main remote heads, local branches/worktrees, PRs 12/14, commit diffs, retained review records, and the master plan. Branch counts below are relative to that starting main; overlapping histories are not additive. The audit added no application logic beyond the reviewed merge, executed no market data or model training, ran no HPO/refit/replay, and did not edit owner-maintained `path.csv`.

This is an evidence-based sample of review effectiveness, not a claim that every historical finding was independently reproduced. Commit counts measure activity, not delivered value; token costs and review labor were unavailable.

## Are we progressing or spinning?
**F1/F2: useful engineering, delayed integration.** The F2 successor branch at `ea82c39` contains 93 commits absent from starting main: 22 touch production Python, 24 touch tests without production Python, and 47 are other changes, predominantly documentation. This includes its shared plan/F1 ancestry. Its final two Terra reviews (24 and 25) found zero unresolved Critical/Major findings, with two minor issues deferred.

Concrete fixes matter: `57beb1f` retains the staged document across adapter imports; `b35ecb5` does the corresponding validation fix; `e239645` prevents snapshot mutation from bypassing execution denial and rejects forbidden execution-node semantics. These address real execution ordering and input-boundary defects. However, discovering the same family across facades in successive rounds shows the initial inventory and correction sweep were incomplete.

Scope matters: this F2 exit covers the delivered parser/public-facade guard slice. It is not evidence that a privileged external launcher, the entire ten-head release pipeline, or every capability named in the F2 plan heading exists. The final evidence says `deployment_eligible=false`.

**F4: valuable early fixes, followed by a boundary problem.** The already-merged lifecycle work exposed real aliasing, partial-write, wrong-stream, receipt-integrity, and restart defects. Review 4 included a Critical cross-stream consume issue; review 10 included a Critical grafted receipt-chain issue. These are meaningful corrections, not cosmetic churn.

But F4's final reviews 14/15 are clean only under main's accepted ADR-0126 single-surface development threat model. Five previously Major findings, R42–R46, remain deferred outside that model. The code did not magically become safe against every combination of in-process mutation. Preserve that qualification wherever F4 is described as approved; production eligibility remains false.

**F5a: the strongest evidence of spinning.** Its branch at `c489199` adds 74 commits beyond starting main: 23 production-Python commits, 23 test-only/test-plus-doc commits, and 28 other commits. Early fixes were useful: reject empty/invalid artifacts, validate exact mappings, make a refused bind atomic, and spend admission before an external write that may raise.

Then reviews 11–23 repeatedly attacked the same single-use admission identity: shallow copying, pickle reconstruction, copied state, mint factories, writable slots, mutable sets, type sentinels, nested cells, and constructor defaults. Each representation changed; the authority still lived in mutable Python state. This is a repeated boundary failure, not thirteen unrelated product capabilities.

The owner already stopped consume-once after review 23; `F5A-R23-ctor-intern-unspend` remains open. Two earlier ReviewExit files exist, but later findings supersede their usefulness for whole-branch closure. The subsequent driver-only design review is also not clean; its revised contract has no later clean verdict. PR 14 must remain open.

**Docstrings: real documentation improvement, disproportionate release gating.** The completed task reports 29 rounds and 24 converted modules; 30 of the original 57 modules remain separate work. I compared executable syntax trees with docstrings removed for the 22 commits explicitly named Round 8 through Round 29. Twenty-one changed no executable Python structure. Only round 15 (`ce06025`) changed runtime behavior, widening connector import-error wrapping to match its sibling seam.

That is 21/22 = 95.5% of this precisely defined late-round sample, not 95.5% of all review effort or all findings. Documentation can be important, particularly a false safety promise, but missing exception cross-references repeatedly labeled Major are not evidence of equivalent operational danger. Round 28 finally did an exhaustive Registry/Lineage call-site inventory; that family-wide sweep should have happened near the first recurrence.

**Clustering/RL: planning progress, no delivered implementation.** Two commits add a 1,110-line proposed plan and a 62-line ADR addition. The plan records 17 correction checkpoints, repeatedly expanding individual test-case enumerations and restarting both lenses. Its seventeenth correction still has no final clean pair. Some findings improved the contract; the reset to existing FittedTransform/sklearn/SB3 seams avoided unnecessary architecture. Repeated one-item enumeration fixes and re-reviewing already-clean design after prose changes are avoidable overhead.

## Consolidation decisions
Completed integration and cleanup are recorded in the execution section below. These are the audited heads and reasons:

- `cursor/r5-f2-correction22-0f39` — `ea82c39`, PR 12: integrate F1/F2 with their final clean evidence. Its 15 reviewed Python code/test blobs match the evidence hashes. Only the decision-log append conflicted; retain both ADR additions.
- `codex/r5-model-release-20260911` — `6d027c4`: ancestor of that F2 successor; remove after the successor lands, rather than treating it as a separate unfinished lane.
- `claude/todo-simple-items-us9czs` — `2a06453`: already contained in main; no second merge needed.
- `codex/r5-forecast-capital-20260911` — `7a71f39`: already contained in main; design/lifecycle progress does not mean the forecast/calibration/capital lane is complete.
- `cursor/r5-f4-capture-lifecycle-0f39` — `07f85d9`: already contained in main; retain its qualified development review record.
- `cursor/r5-f5a-private-plan-0f39` — `c489199`, PR 14: retain; later Major findings and failed Phase 0 prevent closure.
- `codex/cluster-rl-framework-plan-20260913` — `a450d2a`: retain; proposed design, no final clean pair. Its proposed ADR-0122 collides with the accepted model-release ADR-0122 integrated here.
- `codex/r5-replay-ops-20260911` — `97900ef`: retain; owner acceptance of its design is not a retained final independent clean review of the whole branch. Its replay ADR-0126 collides with main's F4 ADR-0126, and its V2 terminal design explicitly says the master plan still needs correction before implementation.

Legacy local-only work is also not silently discarded: `docs/final-model-replay-adr` ends at a round-19 corrective commit, not final closure; `local-wrap-2026-09-07` and the dirty `wrap-tft-rf-runs` checkout need content reconciliation. The two local F1 evidence branches have patch-equivalent successor commits, but their distinct history is retained. A missing remote or an old “fix” message is not enough to delete unmerged local work.

## A better writing and approval system
These are proposals for the next process revision, not silent changes to the existing skills.

1. **Freeze the problem and authority first.** State who may supply hostile data and who controls the interpreter, code, broker, files, and keys. Do not claim a mutable object in the attacker's own interpreter is a security boundary. Separate a development fixture from an external enforcement authority.
2. **Write one contract-to-test inventory before implementation.** For each invariant, list every public entry point, malformed-input family, state transition, forbidden side effect, and positive compatibility case. Keep compound conditions as separate test cases. Reuse existing validators and lifecycle seams instead of copying them.
3. **Fix families, not examples.** A snapshot/order defect triggers a sweep of run, validate, staged, walk-forward, direct constructors, and adapters in one correction. A docstring-propagation gap triggers an inventory of all equivalent callers. Reviewers return all proven findings for their assigned boundary in one report.
4. **Calibrate severity by consequence.** Critical/Major means a demonstrated violation of an approved invariant with material impact: wrong result, data loss, authority bypass, replay/accounting corruption, or important compatibility failure. Documentation becomes a blocker when its false contract can cause such a failure; missing sections and speculative enumeration preferences normally go to a separate backlog. A design finding must explain the concrete bad implementation the contract currently permits. Independent review/adjudication still decides disputed findings.
5. **Make the checkpoint change the work.** The current skill already requires a checkpoint after three failed correction cycles. Repeatedly writing “localized; continue” is not convergence. A recurring family must produce a complete sibling inventory, a smaller slice, or an explicit boundary redesign before another correction.
6. **Review the final candidate with two distinct lenses.** Use correctness/authority and tests/integration lenses on the same code, contract, dependency versions, and threat model. Keep the exact reviewer output, findings, correction, and commands. Sequential dispatch is sufficient; more reviewers are not automatically more assurance.
7. **Lock precisely, then merge promptly.** Zero unresolved in-scope Critical/Major plus relevant checks and a recorded minor backlog closes the candidate. Do not edit nits after the final review. After integration, verify changed code matches the approved candidate and test the interfaces that actually combined. Reopen for changed behavior/contracts/dependencies or a new proven blocker—not because another fresh reader might suggest wording.
8. **Track delivery and review cost separately.** Record gate closed, accepted commit, defect family, newly introduced versus pre-existing bug, severity and consequence, tests added, review time, and merge delay. Report runtime fixes, contract fixes, and documentation fixes separately. Raw rounds and raw “Major” totals obscure the distinction.

A lock records what was checked and under which assumptions; it is not a claim that no future defect exists.

## Ordered completion plan
The controlling development DAG has 24 slices. After this integration, main has retained ReviewExit records for F1, F2, and F4. “Three of 24 records present” is an inventory fact, not a percentage of product functionality; older mechanisms exist, and current exits can cover narrower delivered slices.

1. **Finish the F5a design checkpoint.** Review the revised driver-only contract `0032` against the failed design verdict `0031`. Preserve F4's frozen development boundary. A wrapper cannot make a still-callable broker globally inaccessible. Decide and document the enforceable scope before writing another identity container. Consume-once stays explicitly open until the authority problem is resolved and independently reviewed; do not infer closure from unrelated driver progress.
2. **Finish F5a's remaining declared clusters.** Driver-only capture; signed schema validation; captured authorization set V2; captured-artifact parser grammar; descriptor/receipt equality; session/restart linkage; replay-tape descriptors. Use one acceptance matrix per cluster and family-wide corrections. Then close the whole dependency, including the stopped consume-once issue, before starting dependent F3/F5b.
3. **Reconcile replay design and plan.** Allocate a unique ADR number for the already accepted replay terminal design, update live references without rewriting immutable evidence, and correct R1–R3/I from V1 to V2. Obtain the required independent design verdicts. The existing “accepted” label does not resolve the explicit plan conflict.
4. **Build in dependency order.** F3 event chronology → F5b captured consumers; A1–A4 training/refit/native model release and B0–B3 labels/calibration/scenarios/caps; E1 execution profile and C0 accounting; C1 optimization; then R1–R5 effects, outbox, recovery, monitoring, and realism adapters. C0's declared dependencies are already satisfied, so a separately owned accounting slice can progress while F5a design is resolved, subject to the corrected replay contract. Keep each branch tied to one bounded acceptance result.
5. **Prove integration with synthetic data (I1).** Compare uninterrupted execution with crashes at each persistence/effect boundary. Require equal terminal identities, ledger head, checkpoint, account state, emitted events, returns, and report. Exercise weekly contributions, holds, release rotation, and retraining handoff. Code review alone cannot establish this.
6. **Run the real study only after its separate gates (I2).** Freeze policies, artifacts, runtime/security authorities, and execution permissions first. No real HPO, refit, lockbox, backtest, or trading is authorized by this audit.
7. **Keep clustering/RL off the backtest critical path.** Resolve its ADR collision, complete one whole-plan test-matrix sweep, obtain the final clean pair and outstanding owner/dependency approvals, then implement its small extensions to existing seams. Do not reopen the backtester scope to include it.

## Verification and execution record
The audit used WSL2 and an isolated worktree. Focused integration checks used the existing DSKit virtual environment, with PYTHONPATH set to that worktree.

- Combined relevant pipeline tests: **704 passed, 11 skipped, 1 failed**. The same ordered selection on unchanged main `36553ae` produced **594 passed, 11 skipped, the identical failure**: the shipped-kind registry assertion sees the synthetic `synth-source` test node. Running `test_foreach.py` alone gives **60 passed**. This is a demonstrated baseline/order issue, not a newly hidden green result.
- Changed-path Ruff reports two existing reviewed style issues: D202 in `pipeline/__main__.py` and unused import F401 in `pipeline/stages.py`. They remain deferred under the owner's zero-Critical/Major threshold. No claim of a completely clean lint run is made.
- Fifteen reviewed Python blobs match F2 evidence. F1/F2 evidence chains and reviewer identities were checked; no runtime conflict resolution was needed. The decision-log resolution preserves main's existing ADRs and adds the reviewed model-release ADR-0122.
- The full suite was not run. No application code was changed merely to turn baseline/style issues green.

**Integration completed:** merged and pushed as `f57b0f08d7fb29acb21759c8c87d7945f2c33a69`. Five completed remote branches were deleted atomically with expected-head checks. Thirty-two fully contained local branches were deleted after ancestry and clean-worktree checks. Their worktree directories were preserved and detached at their original commits; dirty and unique local work was retained. Only three non-main remote branches remain: F5a, clustering/RL, and replay design. No branch was force-merged and no unmerged work was discarded.

**Independent integration review, sequential dispatch:** the first reviewer, `/root/integration_correctness`, returned: “PASS — zero proven integration Major/Critical or correctness regressions.” It verified reviewed Python blobs, main preservation, the ADR merge, and F1/F2 hash chains.

The second reviewer, `/root/integration_validation`, returned: “Verdict: **0 Critical, 0 Major, no new correctness findings** on the pending merge of `ea82c39` into `36553ae`, reviewed read-only in `/home/russell/wt/review-progress-20260914`.” It independently ran **25 targeted tests, all passing**, covering import markers, retired API refusal, snapshots, staged identity, and adapter compatibility. It explicitly limited its verdict to the user's merge threshold, not deployment readiness.

**Local branch cleanup inventory:** `audit/replay-ops-gap-20260911`, `chore/point-runs-at-clean-data`, `chore/quote-pull-budget`, `claude/phase1-recovery-seven-gates-ao4zdj`, `codex/forecast-capital-gap-20260911`, `codex/gate3-cashflows-519cde0`, `codex/gate3-cashflows-f74aff8`, `codex/model-release-gap`, `codex/r5-forecast-capital-20260911`, `codex/r5-model-release-20260911`, `codex/replay-ops-sol-20260911-162900`, `codex/skeptic-review-convergence-20260913`, `feat/final-model-gates`, `feat/p14-recurrent-fusion`, `feat/p2-p3-feature-blocks`, `feat/p4-bounce-diagnostics`, `feat/p5-skill-rule`, `feat/p6-measures-p8-bar`, `feat/p7-model-shortlist`, `feat/save-row-predictions`, `fix/hstar-min-split-gain`, `fix/journal-concurrent-append`, `fix/walk-memory-dense-rows`, `glm/gate4-forecast-bundle-7e09d0`, `handoff/forecast-capital-round5-20260911`, `handoff/replay-ops-round5-20260911`, `merge-drivers-skeleton-topic`, `review-mio-doc`, `sol/forecast-capital-round1-20260911`, `sol/model-release-lane1-20260911`, `wip/final-model-replay-phase1-20260909`, `work/main`.


## Reproducibility and handoff
Compare `36553ae..ea82c39` for incoming F1/F2 history and `36553ae..c489199` for F5a. Inspect the retained F1/F2/F4 JSON records under `docs/review-evidence/`; F5a records remain at its retained branch. Use `git show <commit>:<path>` for deleted-branch evidence—the commits remain reachable from main.

Focused test selection: `tests/pipeline/test_{document,planner,node,driver,stages,main,main_adapter_flag,fitted,foreach,selector}.py` plus `tests/pipeline_libs/test_numpy.py`, invoked in that order with `PYTHONPATH=$PWD /home/russell/dskit/.venv/bin/python -m pytest -q`. The docstring sample is exactly the commits titled Round 8–29 on `2a06453`; executable AST comparison removes module/class/function docstrings and ignores formatting/comments.

Next work is the F5a boundary decision and replay-plan reconciliation. This memo is the handoff; unfinished branches retain their original evidence and restrictions.
