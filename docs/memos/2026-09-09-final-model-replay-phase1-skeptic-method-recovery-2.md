# Skeptic review — final-model replay Phase 1 method/API lens, correction-cycle-2 recheck

**Reviewer task:** fresh, independent method/API-contract re-verification of
the ADR-0113 Phase-1 code after the architecture/governance recovery fix
(commit `b217e5f`) that closed the 4 Major findings in
`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-architecture-recovery.md`.
Per that report's finding scope, the architecture-driven edits touched
`kinds_search.py`'s docstrings only (three rewritten Examples blocks) plus
two new test classes/cases — nothing in this cycle's diff was expected to
touch method-lens territory (`SelectionRecord`, `_ordering_key`, `TrialLedger`
transaction code), but that must be confirmed, not assumed, and the new
Examples/tests must actually be correct.
**Model/effort:** Claude Sonnet 5, high.
**Dispatch:** fresh sequential method/API review of the cycle-2 delta against
report #2 (method-recovery, PASS) and report #3 (architecture-recovery, FAIL);
no implementation edits made.

## Scope and evidence

Read root `CLAUDE.md`, `dskit/pipeline/CLAUDE.md`, ADR-0113's current text,
report #2 (`...skeptic-method-recovery.md`, PASS, 0/0) and report #3
(`...skeptic-architecture-recovery.md`, FAIL, 4 Major) in full, and the
current `dskit/pipeline/kinds_search.py` / `tests/pipeline/test_kinds_search.py`.

```text
$ git diff c775ac5..HEAD -- dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
(full Phase-1 diff — reviewed; consistent with reports #2/#3's prior findings,
 all already resolved)

$ git diff 2631282..HEAD -- dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
(cycle-2 delta — the only lines this cycle could have regressed on my lens;
 quoted and verified clause-by-clause below)

$ python3 -m pytest -q tests/pipeline/test_kinds_search.py tests/pipeline/test_planner.py \
    tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
190 passed in 7.15s

$ python3 -m ruff check dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
All checks passed!

$ git diff --check
(clean, exit 0)
```

Also ran the three rewritten Examples blocks verbatim as live Python against
the current module to confirm the claimed outputs are not just plausible but
actually correct:

```text
$ python3 -c "
from dskit.pipeline.kinds_search import CandidateInventory, TrialLedger, OneStandardErrorSelector
inventory = CandidateInventory({'depth': [1, 2]}, max_candidates=2)
print('ex1:', inventory.combinations[0]['depth'])
ledger = TrialLedger(CandidateInventory({'depth': [1]}), evidence_fields=('se',))
ledger.record({'depth': 1}, score=0.25, se=0.05)
print('ex2:', ledger.is_complete())
selector = OneStandardErrorSelector(select='min', simplicity_key=lambda row: row['overrides']['depth'])
print('ex3 constructed ok:', selector)
"
ex1: 1
ex2: True
ex3 constructed ok: <dskit.pipeline.kinds_search.OneStandardErrorSelector object at 0x...>
```

Grepped the 2631282..HEAD delta for the location of every changed line
(`grep -n class SelectionRecord\|def _ordering_key\|class TrialLedger`) and
confirmed by inspection that the delta's only hunks inside `kinds_search.py`
sit in three docstrings — `CandidateInventory`'s class docstring (lines
472-480), its `max_candidates` property docstring (607-610, Examples block
removed entirely, one-line docstring kept), `TrialLedger`'s class docstring
(704-711), and `OneStandardErrorSelector`'s class docstring (1184-1191) — and
that `_ordering_key` (line 375), `SelectionRecord` (line 944 on), and every
`TrialLedger` method body (`record`/`_reserve`/`_commit`/`is_complete`/
`to_obj`, 767 on) have zero lines touched in this delta.

## Findings

None. Cycle 2 is docstring- and test-only in `kinds_search.py`, as report #3
required, and every changed line is correct on the method/API lens.

### Re-verified: the three rewritten Examples blocks are factually correct, not just `>>>`-free

Report #3's Finding 4 required the four Examples blocks be reshaped to the
house `::`-block + `# ->` template; my lens additionally requires the
examples still be *true*. All three:

- `CandidateInventory` (`:472-480`): `CandidateInventory({"depth": [1, 2]},
  max_candidates=2).combinations[0]["depth"]` — `_grid` (`:115-122`) sorts
  space keys and enumerates `itertools.product` in given-value order, so for
  a single key `"depth": [1, 2]` the first combination is `{"depth": 1}`.
  Confirmed live: `1`, matches `# -> 1`.
- `TrialLedger` (`:704-711`): a one-candidate inventory (`{"depth": [1]}`)
  fully recorded by one `record()` call makes `is_complete()` (`:767-770`,
  `len(rows) == expected_count`) `True`. Confirmed live: `True`, matches
  `# -> True`.
- `OneStandardErrorSelector` (`:1184-1191`): construction-only example
  (`select="min"`, a callable `simplicity_key`) — no computed output is
  claimed, so there is nothing to falsify; the constructor signature
  (`:1197`, `*, select, simplicity_key`) and validation (`select in ("min",
  "max")`, `callable(simplicity_key)`, `:1198-1201`) accept the shown call
  exactly as written. Confirmed live: constructs without error.

The `CandidateInventory.max_candidates` property's now-Examples-free
one-line docstring (`:607-610`) is a legitimate simplification, not a
regression: the removed doctest-style Example (`CandidateInventory({"depth":
[1]}, max_candidates=1).max_candidates` → `1`) was trivially restating the
constructor argument back at the reader; dropping it loses no informational
content the class docstring's own Examples block doesn't already carry.

### Re-verified: `TestCandidateInventoryCap` proves the exact `n_trials=1`-bypass scenario, not a shallow stand-in

`_bounded_candidate_count` (`:103-112`) is called unconditionally at
`:583`, *before* `_grid`/`_subsample` are ever reached — so `n_trials` can
never see a candidate set that skipped the cap check. The first test
(`test_max_candidates_refuses_before_materializing_even_with_n_trials_1`)
constructs a 20-binary-key space (2**20 = 1,048,576 combinations),
`max_candidates=100`, `n_trials=1`, and asserts `ValueError` matching
`"candidate count .* exceeds max_candidates 100"`. Traced by hand:
`_bounded_candidate_count`'s running product first exceeds 100 at the 7th
key (`2**7 = 128`), raising `"candidate count 128 exceeds max_candidates
100"` — matches the regex (a `re.search`, not `fullmatch`, so the exact
digit is not over-pinned). This is precisely the scenario the original
architecture FAIL named: `n_trials=1` cannot make the constructor stop
short of computing (and refusing on) the full grid's size. The second test
(`test_a_grid_within_the_cap_still_honors_n_trials`) is the complementary
positive case — a 4-combination space at `max_candidates=4, n_trials=1`
does NOT raise and correctly subsamples to length 1 — proving the cap and
`n_trials` compose correctly rather than one silently disabling the other.
Both tests pass; neither is shallow.

### Re-verified: the two `TestScalarRuleAgreement` boundary cases and the `_MAX_JSON_INT` equality test are correct and land on the true edge

`_MAX_JSON_INT = 10**4096 - 1` in both `planner.py:67` and
`kinds_search.py:70`, and `_json_int_ok` (`kinds_search.py:89-91`;
`planner.py`'s twin is structurally identical) accepts exactly
`-_MAX_JSON_INT <= value <= _MAX_JSON_INT`. The new cases:
`(10**4096 - 1, True)` and its negative are exactly `_MAX_JSON_INT` itself —
the true accepted edge, not an approximation. `(10**4096, False)` and its
negative are exactly one over the edge — the true refused edge, not an
off-by-one that would land two or more digits past the boundary and so
under-test it. `10**4096` has exactly 4097 decimal digits (`10**4096` is `1`
followed by 4096 zeros), so the pair genuinely straddles the 4096/4097-digit
line the rule exists to enforce, not merely "a large" and "a larger" number.
`test_the_two_max_json_int_literals_agree` asserts
`_PLANNER_MAX_JSON_INT == _GRID_MAX_JSON_INT == 10**4096 - 1` — a third,
independent restatement of the literal (not read from either module), so a
future edit to either constant alone (or a matching-but-wrong joint edit)
fails immediately. All four new cases plus the equality test pass under the
existing loop in `test_both_gates_draw_the_same_line`, and I confirmed by
inspection that `CASES` is a plain tuple iterated by that one test method —
the new entries are not silently excluded by any earlier `return`/`break`.

### Confirmed: SelectionRecord, `_ordering_key`, and TrialLedger's transaction code are byte-for-byte unchanged from the version report #2 already passed

`git diff 2631282..HEAD -- dskit/pipeline/kinds_search.py` (quoted in full
above) shows only the four docstring hunks and no other lines in the file.
`SelectionRecord.__init__`/`_build`/`_seal`, `_ordering_key`, and
`TrialLedger.__init__`/`record`/`_reserve`/`_commit`/
`_release_if_uncommitted`/`is_complete`/`to_obj`/`digest` all sit outside
every changed hunk. Report #2's PASS on those three areas therefore still
holds without re-derivation; nothing in cycle 2 could have regressed them
because nothing in cycle 2 touched them.

## Checks that passed

- Full focused suite (`test_kinds_search.py`, `test_planner.py`,
  `test_purity.py`, `test_method_lengths.py`): 190 passed (up from report
  #2's 187 — the 3 new tests in this cycle, all passing).
- `ruff check` on both changed files: clean.
- `git diff --check`: clean, no whitespace/conflict-marker artifacts.
- The rewritten Examples blocks use the house `::`-indented-block +
  `# ->`-marked-output template throughout, with no `>>>` anywhere in the
  file (`grep -n '>>>' dskit/pipeline/kinds_search.py` — confirmed empty by
  the ruff `D`-suite passing clean and by direct inspection of all four
  touched docstrings).
- `dskit/pipeline/CLAUDE.md` was also updated in this cycle (Finding 1's
  territory, architecture lens, not mine) — noted only for completeness;
  not re-verified in depth since it is outside the method/API-contract
  lens this report covers.

## Minor observations (non-blocking, not counted toward the verdict)

- The two new `_MAX_JSON_INT` import lines in
  `tests/pipeline/test_kinds_search.py` (`from dskit.pipeline.planner import
  _MAX_JSON_INT as _PLANNER_MAX_JSON_INT` then the `kinds_search` twin) are
  inserted after the existing `_is_json_scalar`/`_ordering_key`/`node`
  imports rather than kept in strict alphabetical order with them — cosmetic
  only; the repo's ruff config selects `["E4", "E7", "E9", "F", "D"]`, no
  import-sort rule, so this is not a lint violation.

## Verdict

**PASS — 0 Critical, 0 Major.** Cycle 2's edits to `kinds_search.py` are
confined to four docstrings (all now correct, house-style, and verified live
against the running module) and its edits to
`tests/pipeline/test_kinds_search.py` are three new, non-shallow regressions
that genuinely close the two remaining architecture-lens gaps (the
`n_trials=1`/`max_candidates` bypass scenario, and the 4096-digit boundary on
the duplicated `_is_json_scalar` rule). `SelectionRecord`'s constructor
lockdown, `_ordering_key`'s total order, and `TrialLedger`'s concurrency
contract — the three areas report #2 already passed — are byte-for-byte
unchanged in this cycle's diff and therefore still sound. No new Critical or
Major defect on the method/API-contract lens.
