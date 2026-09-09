# Skeptic review — final-model replay Gate 2 architecture/integration/governance lens, cycle 2 (verification)

**Reviewer task:** fresh independent re-verification, on current HEAD
(`b654e97`, branch `claude/phase1-recovery-seven-gates-ao4zdj`), that the
cycle-1 Major finding recorded in
`docs/memos/2026-09-09-final-model-replay-gate2-skeptic-architecture.md`
is genuinely and correctly resolved — not merely fixed-looking. Per
`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`
and ADR-0114, this is the last bounded correction cycle: a FAIL here stops
the process for human adjudication instead of looping again.
**Model/effort:** Claude Sonnet 5 (`claude-sonnet-5`), high.
**Lens:** architecture / integration / governance (same lens as cycle 1;
duplication-that-diverges, tiering/purity, OOP pillars, encapsulation
boundary, doc-sibling sync, journal/decisioning, and any new issue in the
fix delta itself).
**Dispatch:** fresh retained review; no implementation edits made. Has no
memory of the cycle-1 session — read that report, ADR-0114, and the fix
commits fresh for this verification.

## Scope and evidence

Read in full: my own cycle-1 report, ADR-0114 §2/§5/§11.1
(`docs/architecture/decision-log.md`), the final-model plan, root
`CLAUDE.md`, `dskit/pipeline/CLAUDE.md`, the complete diff
`a9a3112..HEAD` restricted to `final_model.py` and its test file, the
complete diff of commit `b654e97` (the test-only follow-up), and the
actual content of `children/intraday_equities/configs/run-final-hpo.json`.

```text
$ git diff --stat a9a3112..HEAD
 .../intraday_equities/final_model.py               | 116 +++++++--
 .../intraday_equities/tests/test_final_model.py    |  85 ++++++-
 ...inal-model-replay-gate2-skeptic-architecture.md | 258 ++++++++++++++++++++
 ...09-final-model-replay-gate2-skeptic-method-2.md | 151 ++++++++++++
 ...9-09-final-model-replay-gate2-skeptic-method.md | 268 +++++++++++++++++++++
 dskit/pipeline/libs/sklearn.py                     |   3 +
 6 files changed, 846 insertions(+), 35 deletions(-)

$ git log --oneline a9a3112..HEAD
b654e97 test(gate2): close hpo_space()'s remaining refusal-branch coverage gap
c7938d8 docs(memos): retain Gate 2 architecture/governance review, cycle 1 (FAIL)
4f38f12 fix(gate2): read the HPO grid from its real source, not a hardcoded copy
da5d898 docs(memos): retain Gate 2 method/API-contract review (PASS)

$ python3 -m pytest -q tests/pipeline_libs/test_sklearn.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
237 passed, 9 skipped, 8 warnings in 11.94s

$ python3 -m ruff check dskit/pipeline/libs/sklearn.py dskit/pipeline/node.py \
    dskit/pipeline/kinds_table.py tests/pipeline_libs/test_sklearn.py \
    children/intraday_equities/intraday_equities/final_model.py \
    children/intraday_equities/tests/test_final_model.py
All checks passed!

$ git diff --check
# clean, no output

$ cd children/intraday_equities && PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m pytest -q tests/test_final_model.py
34 passed in 1.25s

$ git diff --stat c775ac5..HEAD -- children/intraday_equities/docs/decisioning/path.csv
# empty — untouched
```

Additional evidence gathered for this cycle:

- Read `configs/run-final-hpo.json`'s `stages.finalist.params.templates`
  directly (not via the code under review): index 0 has `id: "lgbm"`,
  `family: "pooled-lightgbm"`, and
  `model.hpo_space == {"learning_rate": [0.003, 0.01, 0.03], "num_leaves":
  [4, 8, 16], "min_child_samples": [500, 1000, 2000, 4000], "reg_lambda":
  [10.0, 100.0, 1000.0], "reg_alpha": [0.0, 0.1, 1.0]}` — the exact shape
  and values `hpo_space()` now reads and returns.
- `grep -rn "HPO_SPACE\b"` (word-boundary) across the whole repo — zero
  matches. The module constant is gone everywhere, not just renamed.
- `grep -rln` for the literal value fingerprint (`0.003, 0.01, 0.03` /
  the `min_child_samples` ladder) across all `.py` files — the only hit
  is `children/intraday_equities/tests/test_final_model.py`'s
  `REAL_HPO_SPACE`.
- Diffed `hpo_space()` (`final_model.py:233-306`) against
  `lean_feature_drop()` (`final_model.py:145-229`) line-by-line: same
  default-path construction (`_DEFAULT_*_CONFIG`, `os.path.normpath` off
  the package parent dir), same `try/except OSError -> ValueError`
  wrapping with `"cannot read {path!r} ({exc})"`, same nested `.get()`
  descent to `templates`, same `[t for t in templates if isinstance(t,
  dict) and t.get("id") == ...]` filter, same `len(matches) != 1 ->
  "must declare exactly one ... found {len(matches)}"` refusal, same
  family/shape checks before returning an immutable-flavored copy
  (`tuple(drop)` / `{name: list(values) ...}`).
- Read `dskit/pipeline/kinds_search.py:427-598`
  (`CandidateInventory.__init__`) in full: it validates the RAW grid
  shape it is handed (non-empty dict, no dup keys, JSON-scalar values,
  digit-length caps, no dup values per key) — a structurally different
  concern from what `hpo_space()` validates (finding the right template
  INSIDE a config document and confirming its `id`/`family` match
  ADR-0114). No logic is restated between the two: `hpo_space()` never
  re-checks JSON-scalar-ness or digit caps, and `CandidateInventory`
  never knows what a "finalist template" is. Same non-overlap pattern
  `lean_feature_drop()` already has against `ColumnSubsetEstimator`'s own
  param validation.
- Reviewed `b654e97`'s full diff: test file only (plus a memo). No
  production file touched. Adds 4 refusal tests
  (missing-file/duplicate-template/wrong-family/malformed-grid),
  bringing `hpo_space()` to parity with `lean_feature_drop`'s existing
  refusal-test count, addressing the method reviewer's cycle-2 Minor.
- Re-ran the journal render: `python -m dskit.journal render --root
  children/intraday_equities` still reproduces
  `docs/decisioning/README.md` byte-for-byte on current HEAD
  (`git status --porcelain` on that file empty afterward). `path.csv`
  diff `c775ac5..HEAD` is empty.
- Diffed `dskit/pipeline/{README.md,AGENTS.md,CLAUDE.md}` at `a9a3112`
  vs current HEAD: byte-identical (all three). Confirmed separately that
  none of the three ever described `HPO_SPACE`/`hpo_space` at the
  fine-grained-constant level in the first place (only the bundle-API
  paragraph, per cycle-1's item 6), so there is no sibling-doc drift risk
  from this fix to check — none of the three needed to change, and none
  did.
- `git diff a9a3112..HEAD -- dskit/pipeline/libs/sklearn.py` is a 3-line
  docstring-only addition (`write_bundle`'s `OSError` clause), bundled
  into `4f38f12` to close the method reviewer's cycle-1 Minor findings in
  the same commit — purely additive documentation, no behavior change,
  no interaction with the HPO-grid fix.

## Findings

**Original Major — RESOLVED.** Cycle 1's finding was that
`final_model.HPO_SPACE` was a hand-copied module constant duplicating
`configs/run-final-hpo.json`'s real `hpo_space`, with nothing pinning the
two together, while `lean_feature_drop` correctly read its source at call
time. Commit `4f38f12` replaces the constant with `hpo_space(config_path=
None)`, which:

1. Reads `configs/run-final-hpo.json` (or a caller-supplied path) fresh on
   every call — no caching, no drift window between reads.
2. Locates `stages.finalist.params.templates[]`, filters to `id ==
   "lgbm"`, requires exactly one match, requires `family ==
   "pooled-lightgbm"`, and requires `model.hpo_space` to be a non-empty
   mapping of dimension -> non-empty value list — refusing (`ValueError`)
   on any shape mismatch rather than silently returning a wrong or
   partial grid.
3. Independently confirmed against the real file content (read directly
   above, not through the function under test): the config's actual
   `hpo_space` at `stages.finalist.params.templates[0].model.hpo_space`
   is exactly the five-dimension, 324-combination grid the function
   returns. This is not a coincidence of current values matching a stale
   copy — there is no stale copy left to coincide with.
4. `build_candidate_inventory` and `boundary_flags` were both updated to
   call `hpo_space()` instead of referencing the deleted constant
   (verified in the diff — both call sites changed, no third call site
   missed).

This is the genuine architectural fix cycle 1's suggested-fix option (a)
named — a `config_path=` mirror of `lean_feature_drop`'s exact pattern —
not a renamed constant, not a memoized/cached read that could still
drift, and not merely a pinning test bolted onto the old constant. The
duplication-that-diverges shape cycle 1 flagged no longer exists: there is
exactly one place in production code that knows the grid's values (the
config file), and exactly one function that reads it.

The test file's `REAL_HPO_SPACE` (a hand-typed literal used only to
assert `hpo_space() == REAL_HPO_SPACE`) is the sole remaining place the
values are written a second time anywhere in the repo, and it is
correctly scoped to the test file, never imported by production code. I
agree this is the CLAUDE.md-sanctioned "deliberate independent
restatement" case, not a recurrence of the original defect: a test
asserting `hpo_space()` against a value sourced from `hpo_space()` itself
would assert nothing (it would pass even if the function were hardcoded
or read the wrong field), so an independently-transcribed expected value
is required for the test to be meaningful. This is exactly the same
shape as `REAL_LEAN_DROP`, which cycle 1 already examined and accepted
for `lean_feature_drop` under the same rule — applying it consistently to
`hpo_space`'s parallel test is correct, not a double standard.

**No new architecture/governance issue found in the fix delta or in
`b654e97`.**

- `hpo_space()` does not duplicate any validation `CandidateInventory`
  already owns (see evidence above) — it validates config-DOCUMENT shape
  (is there one matching template, does it declare the right family, is
  its `hpo_space` field present and well-formed), which is a strictly
  prior, structurally different concern from `CandidateInventory`'s own
  validation of the raw grid VALUES it is handed. Deferring the latter to
  `CandidateInventory` (which `build_candidate_inventory` does, unchanged)
  rather than re-deriving it in `hpo_space()` is the correct division of
  labor, matching `lean_feature_drop`'s existing non-overlap with
  `ColumnSubsetEstimator`.
- Error-handling style is consistent with `lean_feature_drop`: same
  exception type (`ValueError`), same message vocabulary (`"cannot
  read"`, `"must declare exactly one ... found N"`), same control flow
  shape (early return-by-raise, no partial success). The one structural
  difference — `hpo_space` checks `family` in its own guard clause
  separate from the shape check, where `lean_feature_drop` checks
  `family` and `estimator` together in one guard — is a difference
  forced by the two functions checking different numbers of fields (one
  vs two), not an inconsistency; each still produces one refusal per
  distinct failure mode with a message naming what failed.
- `b654e97` is exactly what its commit message claims: test-only,
  additive refusal-branch coverage, no production code touched, no
  behavior change. It closes a Minor from the OTHER (method-lens)
  reviewer's cycle 2 and does not bear on this lens's Major.
- The `sklearn.py` 3-line docstring addition bundled into `4f38f12` (the
  `write_bundle` `OSError` clause) is out of this Major's scope but
  reviewed for safety: additive documentation only, closes the method
  reviewer's own Minor findings, no code or behavior change, no
  interaction with `final_model.py`.

## Checks that passed (re-confirmed on current HEAD)

1. **Tiering/purity.** `tests/pipeline/test_purity.py` passes (folded
   into the combined run above). `final_model.py`'s only heavy import
   (`numpy`) remains inside a function body; `hpo_space()`'s own imports
   (`json`, `os`) are stdlib, already at module top before this fix, and
   the fix adds no new module-top import.
2. **OOP pillars / no branching regression.** `hpo_space()` is a plain
   function mirroring an existing sibling pattern (root CLAUDE.md's
   "prefer objects" rule applies to BEHAVIOR that varies by case; this is
   a single read-and-validate helper with one call shape, the same
   category `lean_feature_drop` already occupies without objection in
   cycle 1). No new `if kind ==`/`if mode ==` branching introduced
   anywhere in the diff. `EstimatorBundle`'s design is untouched by this
   cycle's diff (confirmed: `sklearn.py`'s only change is the 3-line
   docstring addition, nothing touching `EstimatorBundle`'s class body).
3. **Encapsulation boundary.** `hpo_space` is correctly public
   (exported in `__all__`, replacing `HPO_SPACE`'s old export slot,
   alphabetically placed between `cluster_scores_by_day` and
   `lean_feature_drop`, consistent with the list's existing ASCII-sort
   convention). No new underscore-prefixed helper was introduced or
   leaked.
4. **Package doc sibling sync.** `dskit/pipeline/{README.md,AGENTS.md,
   CLAUDE.md}` are byte-identical to their `a9a3112` state — confirmed
   by diff — and none of the three ever described the HPO grid at the
   constant-vs-function level of detail cycle 1's Major concerned, so
   there was nothing for this fix to leave stale. No child-level
   `README.md`/`AGENTS.md`/`CLAUDE.md` reference to `HPO_SPACE` exists to
   go stale either (grepped: none).
5. **Journal/decisioning.** `docs/decisioning/path.csv` diff
   `c775ac5..HEAD` remains empty — untouched by either correction
   commit, as required (agents never edit it). `docs/decisioning/
   README.md` is still exactly reproduced by `dskit.journal render` on
   current HEAD (verified independently, not merely by re-reading the
   git history) — the correction commits added no new journal-worthy
   deliverable (no new capability shipped, only a defect fix plus test
   coverage), so no new `actions.csv` row was required or expected, and
   none was hand-added.
6. **Node.py/kinds_table.py scope call (re-confirmed unaffected).**
   `git diff --stat a9a3112..HEAD` shows zero further changes to either
   file in this correction cycle — my cycle-1 independent judgment that
   the `atomic_write` promotion was a legitimate, narrow, behavior-
   preserving consolidation stands unchanged because nothing about it
   changed.
7. **All required commands pass on current HEAD** exactly as listed in
   Scope and evidence above: 237 passed/9 skipped (sklearn pack + purity
   + method-lengths), ruff clean across every named file, `git diff
   --check` clean, 34 child tests passed (was 29 at cycle-1 HEAD, now
   including the 4 new refusal tests plus the renamed/expanded main
   test), `path.csv` diff empty.

## Verdict

**PASS — 0 Critical, 0 Major, 0 Minor.** The cycle-1 Major
(`final_model.HPO_SPACE` as an unpinned second transcription of
`configs/run-final-hpo.json`'s real grid) is genuinely resolved: commit
`4f38f12` replaced the constant with `hpo_space(config_path=None)`, a
call-time reader that mirrors `lean_feature_drop`'s established pattern
exactly and was independently verified against the actual config file
content, not merely against the function's own output. No hardcoded copy
of the grid values remains anywhere in production code; the test file's
`REAL_HPO_SPACE` is a correctly-scoped, CLAUDE.md-sanctioned independent
test fixture, applying the same standard cycle 1 already approved for
`REAL_LEAN_DROP`. The test-only follow-up (`b654e97`) closes an unrelated
Minor from the method lens and introduces no new risk. Every other
cycle-1 "checks that passed" item was re-verified independently on
current HEAD and still holds; no new architecture or governance issue
was found in the fix delta. This closes Gate 2's architecture/governance
lens.
