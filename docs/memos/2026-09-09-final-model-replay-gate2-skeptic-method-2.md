# Skeptic review — final-model replay Gate 2 method/API-contract lens, cycle 2

**Reviewer task:** re-run of the method/API-contract lens (cycle 1:
`2026-09-09-final-model-replay-gate2-skeptic-method.md`, PASS) after the
cycle-2 fix commit `4f38f12`, which resolved the architecture lens's
cycle-1 Major finding (hardcoded `HPO_SPACE` constant instead of
reading `configs/run-final-hpo.json`). Branch
`claude/phase1-recovery-seven-gates-ao4zdj`, HEAD `c7938d8`.
**Model/effort:** Claude Sonnet 5 (`claude-sonnet-5`), high.
**Dispatch:** fresh retained method/API-contract review; no implementation
edits made.

## Scope and evidence

Read in full: `children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`,
ADR-0114 (`docs/architecture/decision-log.md`, Phase 2 + §11 item 1), the
cycle-1 architecture report (FAIL, 1 Major) and my own cycle-1 method
report (PASS, 2 Minor) in full, and the complete cycle-2 delta:

```text
$ git diff a9a3112..HEAD --stat
 .../intraday_equities/final_model.py               | 116 +++++++--
 .../intraday_equities/tests/test_final_model.py    |  38 ++-
 ...skeptic-architecture.md (new, cycle-1 report)   | 258 +++
 ...skeptic-method.md (new, cycle-1 report)         | 268 +++
 dskit/pipeline/libs/sklearn.py                     |   3 +
 5 files changed, 648 insertions(+), 35 deletions(-)
```

Independently loaded `children/intraday_equities/configs/run-final-hpo.json`
and inspected its `stages.finalist.params.templates[]` structure by hand
(not via the function under test) to confirm the real JSON shape before
judging the new parser against it.

```text
$ python3 -m pytest -q tests/pipeline_libs/test_sklearn.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
237 passed, 9 skipped, 8 warnings in 12.20s

$ python3 -m ruff check dskit/pipeline/libs/sklearn.py dskit/pipeline/node.py \
    dskit/pipeline/kinds_table.py tests/pipeline_libs/test_sklearn.py \
    children/intraday_equities/intraday_equities/final_model.py \
    children/intraday_equities/tests/test_final_model.py
All checks passed!

$ git diff --check
# clean, no output

$ cd children/intraday_equities && PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m pytest -q tests/test_final_model.py
30 passed in 1.32s
```

## Cycle-1 conclusions re-confirmed unchanged

`dskit/pipeline/libs/sklearn.py`'s cycle-2 delta is exactly the 3-line
`Raises` addition to `write_bundle`'s docstring (`OSError` note) —
`write_bundle`, `load_bundle`, `EstimatorBundle`, and `atomic_write`
themselves are byte-identical to `a9a3112`, confirmed via the diff above
(no other hunk touches `sklearn.py`, `node.py`, or `kinds_table.py`). All
nine "Checks that passed" items from the cycle-1 report — bundle digest
scope, atomicity, path-escape refusal, ADR-0114 §11 item 1 compliance,
the P16 lean mask, the search-machinery isolation test, docstring/ruff
cleanliness, package-doc currency, and the deliverable-3 deferral claim —
concern code untouched by this delta and stand as verified.

## Independent verification of the new code: `hpo_space()`

**Config shape match, verified against the real file, not the
docstring.** `configs/run-final-hpo.json`'s `stages.finalist.params.templates[]`
contains a `{"id": "lgbm", "family": "pooled-lightgbm", "model": {...,
"hpo_space": {...}}}` entry — `hpo_space()`'s read path
(`document["stages"]["finalist"]["params"]["templates"]` → filter
`id == "lgbm"` → check `family == "pooled-lightgbm"` → read
`model["hpo_space"]`) matches this exactly. `hpo_space()` returns the
same 5-dimension/324-combination grid the cycle-1 report already
hand-verified against the file (`learning_rate`/`num_leaves`/
`min_child_samples`/`reg_lambda`/`reg_alpha`).

**Validation logic, independently exercised beyond the shipped tests.**
Constructed six malformed documents by hand and called `hpo_space()`
against each (not merely read the code): two matching `"lgbm"` templates,
a `family` other than `"pooled-lightgbm"`, a template with no
`hpo_space` key, an empty `hpo_space` dict, a dimension mapped to an
empty list, and a dimension mapped to a non-list value. All six refuse
with the correct, distinct `ValueError` message; a missing file also
refuses cleanly via the `OSError`→`ValueError` re-wrap (mirrors
`lean_feature_drop`'s own pattern exactly). No bug found in the function
itself.

**`hpo_space()` mirrors `lean_feature_drop` structurally** (read file,
filter by `id`, check family, extract and shape-check the payload,
return a plain value) — the ADR-0114 §11.1 fix this cycle exists to make
now has the same shape as the P16 mask's already-approved pattern, not
merely the same intent.

**`_dump_bundle_joblib`/`write_bundle`'s new `OSError` `Raises` line is
accurate.** Traced the call chain: `atomic_write` (`node.py:173-232`)
catches `BaseException` only to unlink its temp file, then re-raises the
original exception unchanged (bare `raise`) — no wrapping. `write_bundle`
calls `_dump_bundle_joblib` (line 1114) and the manifest's `atomic_write`
(line 1116) with no surrounding `try`/`except` of its own. An `OSError`
from either therefore propagates out of `write_bundle` unchanged, exactly
as the new docstring line now states.

## Findings

No Critical or Major findings survive verification. One new Minor
finding (this cycle's new code); the two Minor findings from cycle 1
were both closed by this delta and are not restated.

### Minor — `hpo_space()`'s refusal paths are under-tested relative to its own sibling, `lean_feature_drop`

`hpo_space()` implements (and, per the six hand-constructed cases above,
correctly implements) four distinct refusal conditions: not-exactly-one
matching template, wrong `family`, and two shapes of malformed
`hpo_space` (missing/non-dict/empty, and a non-list or empty value
list). The shipped test suite exercises exactly **one** of these —
`test_hpo_space_refuses_a_config_with_no_lgbm_template` (zero matches
only) — plus the happy path. It does not test: a missing config file, a
multiple-match template list, the wrong-`family` branch, or any
malformed-`hpo_space` shape. By contrast `lean_feature_drop` — the
pattern `hpo_space()` was built to "mirror exactly" — carries three
refusal tests of its own (missing file, malformed
family/estimator, zero-or-multiple templates) covering the parallel
branches. This is a coverage gap, not a functional defect (the code was
independently exercised above and is correct); it does not block this
gate, but it leaves three of four `hpo_space()` failure modes pinned
only by manual review, not by the suite, so a future edit to those
branches could regress silently. Suggested fix: add
`test_hpo_space_refuses_a_missing_file`,
`test_hpo_space_refuses_the_wrong_family`, and a malformed-`hpo_space`
case, matching `lean_feature_drop`'s existing test density.

## Verdict

**PASS — 0 Critical, 0 Major, 1 Minor.** The cycle-1 area (`write_bundle`/
`load_bundle`/`EstimatorBundle`/`atomic_write`) is unchanged except for
one accurate docstring addition, verified by tracing the exception path
rather than trusting the new text. The new `hpo_space()` function reads
the real config at the correct path, matches the actual on-disk JSON
shape (verified against the file, not the docstring), and its validation
logic was independently probed with six hand-built malformed documents
plus a missing-file case — all refuse correctly with no bug found. `grep`
confirms no leftover reference to the removed `HPO_SPACE` constant in
either file, and `REAL_HPO_SPACE` in the test file is confirmed to be an
independently hand-typed literal, not an import of anything from
`final_model.py`. All required commands (focused pytest, ruff, `git diff
--check`, the child's own test run) pass clean. The one Minor finding — a
test-coverage asymmetry against `hpo_space()`'s own sibling function —
is a completeness gap, not a correctness defect, and does not block this
gate.
