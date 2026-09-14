# Focused test-driven development

Use implementation-workflow.md to select a bounded result and check existing
seams, dependencies, authority, and approved contracts before writing code.

## When required

Features, bug fixes, refactors, and behavior changes use RED → GREEN → refactor.
A high-risk change first needs skeptic-review.md Phase 0. Pure documentation,
formatting and static metadata changes use proportionate validation, not invented
behavior tests. Configuration changes that affect behavior require the relevant
validation/identity/behavior tests. Existing owner approvals persist; do not ask
again for work already within the authorized scope.

## RED

Write the smallest failing test that demonstrates the missing behavior. Run it
and record the expected assertion failure on the unchanged implementation,
including command, interpreter and base commit. An import/setup error is not
the intended RED; fix the environment or test before implementing.

Use the contract matrix: separate compound conditions into independent cases,
cover equivalent public entry points, and assert forbidden effects do not occur.
Test results and boundaries, not the internal representation you plan to write.
A regression should fail on the actual defect, and a valid compatibility case
should still work. Parameterization is useful for a whole defect family.

For a test-isolation/test-quality fix, RED may be the minimal same-process
sequence that exposes contamination, or a controlled negative probe proving a
guard can miss its promised violation. Preserve the guard's real coverage;
do not exclude a failing case just to make the suite pass.

If an existing test is already green, investigate whether the behavior is already
implemented. Do not manufacture a failure. Reuse the existing coverage and
record any genuine remaining gap. Preserve others' work and historical evidence;
never delete an existing implementation or somebody else's changes to stage RED.

## GREEN and refactor

Make the smallest generic change that satisfies the accepted contract. Prefer
existing validators, strategies and lifecycle owners over copied rules. Fix the
whole inventoried family within scope; do not add unrelated features.

Run the new regression and affected tests. Refactor only to improve the agreed
seam; keep behavior and tests green. New expected behavior requires another RED.
Do not write one test per helper when it only mirrors implementation.

For failures outside the intended change, rerun the same focused command on the
unchanged base using the same environment and order. A proven baseline failure
is disclosed separately; it is not silently fixed, hidden, or automatically a
new feature blocker. Required CI/check policies still apply. Do not blanket-skip
tests or disable checks to obtain a green result.

## Review and finish

Run relevant affected tests and checks, not the full suite unless directly
justified by touched code or explicitly requested. Broaden testing only for a
new failure, change, or unresolved risk. Record intentionally skipped work.

Freeze the candidate and use skeptic-review.md's two-lens review, family-wide
correction, convergence checkpoint, and lock rules. Documentation-only/evidence
appends do not restart unchanged code approval. Review applies before readiness,
not before every local RED/GREEN checkpoint commit.

Evidence should connect invariant → failing proof → correction → passing proof.
Record new versus pre-existing defects and retain exact commands/results.
Then use wrap.md for already-authorized merge, push and cleanup.
