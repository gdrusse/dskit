# Claude trial: remove the test-registry order dependency

Start Claude on current origin/main, not inside an old shared checkout.
For local WSL2 use the prepared /home/russell/wt/claude-workflow-trial-20260914
worktree; for a cloud agent select gdrusse/dskit at current main. Older checkouts
retain their old startup hooks until updated; a prompt runs after those hooks.
Copy the prompt below. This trial does not advance the blocked F5a branch.

---

Work in WSL2 on gdrusse/dskit. Use the prepared isolated
/home/russell/wt/claude-workflow-trial-20260914 worktree if present and clean.
Otherwise fetch and create your own worktree/branch from current origin/main.
Do not use or change /home/russell/dskit's dirty checkout. Verify your trial
base is current origin/main before editing. Read the current root CLAUDE.md, nearest applicable instructions,
docs/RE-ENTRY.md, docs/skills/implementation-workflow.md, and its TDD, skeptic,
and wrap procedures. Read the audit memo's verification section:
docs/memos/2026-09-14-review-progress-and-consolidation.md.

**One bounded task:** fix the known order-dependent pipeline test failure:
test_foreach.py::TestSearchSpaceFanOut::test_no_shipped_kind_but_the_search_ones_accepts_a_space_param
fails on synth-source after adapter tests have populated DEFAULT_NODE_KINDS,
while test_foreach.py alone passes. The fixture
tests/pipeline/synth_adapter.py registers SynthEvents at import time. Diagnose
the exact lifetime/import-cache/registry interaction rather than assuming a fix.

**Scope:** existing tests/pipeline test/fixture files needed to correct this
isolation/coverage defect, plus an explicitly authorized short trial memo under
docs/memos and the normal RE-ENTRY update. Prefer existing fixture seams. No
production-code, feature, dependency, ADR, registry API, policy or approval-rule
change. If this cannot be fixed honestly within that scope, retain the evidence
and report the smallest needed scope change; do not broaden silently.

**Acceptance:**
- Reproduce the existing failure with a minimal same-process test sequence
  before changing anything. Record command, base commit, and expected assertion.
- Use the existing /home/russell/dskit/.venv/bin/python with PYTHONPATH pointing
  at your worktree; verify imports come from that worktree.
- Preserve real adapter import/registration behavior. Keep the shipped-kind
  validation exhaustive and prove it still catches an invalid real shipped
  non-search kind. No hardcoded synth-source exclusion, blanket skip, weakening
  of assertions, or production API change to satisfy tests.
- Check test-module collection, registry restoration and Python module-cache
  state together. Prove the adapter remains reachable on a subsequent use,
  not just that clearing a registry hides this failure.
- Run the minimal reproducer, relevant adapter and foreach tests in both
  execution orders, and the affected focused group. Do not run the full suite.
  The audit's earlier group was:
  tests/pipeline/test_document.py tests/pipeline/test_planner.py
  tests/pipeline/test_node.py tests/pipeline/test_driver.py
  tests/pipeline/test_stages.py tests/pipeline/test_main.py
  tests/pipeline/test_main_adapter_flag.py tests/pipeline/test_fitted.py
  tests/pipeline/test_foreach.py tests/pipeline/test_selector.py
  tests/pipeline_libs/test_numpy.py.
  Use this group once after the minimal fix if it remains the justified
  integration boundary; do not repeat it without new evidence.
- Run relevant lint/diff checks. Disclose existing unrelated style/baseline
  issues separately; don't fix the audit's unrelated lint or F2 documentation
  nits in this trial.

Follow focused RED/GREEN. Freeze the candidate and run two fresh independent
Claude skeptic contexts sequentially: test-isolation/correctness, then
coverage/compatibility. Retain their actual output, reviewed commit,
commands/results, findings and dispositions in the trial memo. Use the exact
Claude model/version for attribution; do not invent a model identity. Do not
review your own patch or keep adding rounds after both lenses are clean.

Batch any proven material findings across the affected fixture family. After
three failed correction cycles, perform the independent convergence checkpoint;
no fourth example-by-example patch. Do not call a partial result complete.

I authorize committing this bounded work, merging it to main when the final
candidate has zero unresolved Critical/Major, relevant tests and required
checks, pushing main, deleting this trial's contained branch safely, updating
RE-ENTRY, and wrapping. Do not ask for those permissions again. Record deferred
minor issues; no nit edits after lock. Fetch before integration; if main changes
the reviewed boundary, recheck/review that candidate before pushing. Never
force main or bypass protection. Verify remote commit/PR state.

If genuinely blocked, preserve the bounded trial branch (pushing that branch
is authorized), record the blocker, and do not merge. Leave all other branches,
F5a's open Major and frozen F4 code alone. No market data, HPO/refit, replay,
backtest, lockbox access, paper/live action, or deployment.

Finish with the trial memo, before/after evidence, number of review/correction
cycles, final commit, merge/push status, and a brief assessment of whether the
new workflow converged.
