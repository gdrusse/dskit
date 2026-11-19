# Documentation audit against code — 2026-11-17

## TL;DR

Corrected stale guidance and invalid command examples against the shipped code.
Focused checks and both independent reviews passed; historical results were not rerun.

Owner requested a repository-wide Markdown cleanup while MIO work continued.
Base: `78c18df950444d6563d1700405ad38a2dcf1ead7`; isolated branch
`codex/docs-code-audit-20261117`. Existing Claude branches and jobs were not edited.

## Scope and corrections

Inventory and mechanical reference/command checks covered all 785 tracked
Markdown files (about 18 MiB). Current guides received code-grounded corrections;
dated results, ADR decisions, source pins and append-only history were preserved.
This is not a revalidation of historical experiment results or external websites.

- Root quick starts: onboarding root propagation; valid halt reason; six packages.
- Four child walkthroughs: 16 broken shell continuations and required promotion flags.
- Child index: shipped `foreach`, research-note paths and missing child entries.
- Crypto, portfolio and index-options introductions: reconcile stale scope/status
  against shipped modules and their existing lane evidence.
- Pipeline identity: include the excluded `tracking` section.
- Journal guides: correct latest-10 display wording in the source template and
  regenerate all ten child READMEs; both CSV ledgers in every child stay unchanged.
- TODO: distinguish shipped production components from remaining migration work.
- Archived re-entry navigation: explain original link bases without editing snapshots.

## Verification and limits

Run the focused documentation and journal store/CLI checks:
`PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider tests/docs tests/journal/test_store.py tests/journal/test_main.py -q`.
The checkpoint run passed 13 tests; no full suite, model run or network pull.
250 command examples passed argument parsing after placeholder normalization,
without dispatching verbs. The initial scan skipped 30 additional command forms;
the integration reviewer checked all 30 (16 verb groups, seven optional-argument
synopses, five commands with semicolons in comments, and two shell pipelines). No broken local path link or commented-out
shell continuation remains in current/plan docs. All 20 CSV ledgers match the
base. Instruction-pair parity and `git diff --check` also passed.

The tracked historical snapshots retain old relative links, proposals and
machine-local artifact pointers. Local absolute links were checked for presence,
not used to read results. No policy, study window, owner Path row or runtime
algorithm changed; `dskit/journal/render.py` changes only generated prose.
Final candidate, review outputs and mechanical scan details are retained in
the clone's `.git/docs-code-audit-20261117/` audit record.

## Review and handoff

Correctness and integration reviewers independently passed candidate `62c2c67e`
with zero Critical/Major/Minor issues. Three earlier accuracy findings were fixed
in that candidate; the remaining coverage-wording Nit is corrected above.
The final wrap changes only evidence and lane handoff prose; reviewed
runtime/configuration/ledger blobs remain identical. The initial duplicate-name
sweep timed out at its eight-second limit; a 30-second rerun completed with no
duplicate audit found. Final evidence snapshots and integration receipts are in
`.git/docs-code-audit-20261117/`, including both reviewer transcript references.
