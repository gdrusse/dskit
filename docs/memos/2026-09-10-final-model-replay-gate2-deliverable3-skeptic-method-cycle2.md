# Skeptic review — final-model replay Gate 2 deliverable 3 (ADR-0115) method/API-contract lens, cycle 2

**Reviewer task:** fresh, independent skeptic review of commit `73cbbc6`
("fix(gate2): ADR-0115 method review cycle 1 -- report selected
candidate's real score"), the implementer's response to the cycle-1
method report (`docs/memos/2026-09-10-final-model-replay-gate2-deliverable3-skeptic-method.md`,
FAIL: 1 Major, 2 Minor). Branch `claude/phase1-recovery-seven-gates-ao4zdj`,
HEAD `73cbbc6`. No memory of the prior review session; verified everything
from scratch by reading code, not the commit message.

## Scope reviewed

Full diff of `73cbbc6` (`git show 73cbbc6`):
`children/intraday_equities/configs/run-final-hpo.json` (1 line),
`children/intraday_equities/intraday_equities/final_model.py` (`hpo_space()`,
2 lines), `children/intraday_equities/intraday_equities/nodes.py`
(`_hpo_evidence_selection`, 10 net lines). No test files were touched by
this commit. Also read: `dskit/pipeline/kinds_search.py` in full for
`CandidateInventory`/`TrialLedger`/`SelectionRecord`/`OneStandardErrorSelector`
(uniqueness, key, and freezing mechanics), the caller site of
`_hpo_evidence_selection` in `nodes.py` (~5815-5891), the whole of
`configs/run-final-hpo.json`, and `tests/test_nodes.py`'s
`test_no_information_scan_hpo_evidence_builds_a_ledger_and_leaves_the_default_path_untouched`.

Commands run:

```
$ PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m pytest -q tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
5 failed, 257 passed, 12 skipped
FAILED test_run_docs_do_not_restate_the_cohort
FAILED test_every_run_uses_one_local_mlflow_experiment
FAILED test_the_child_installs_what_its_tracking_sinks_need
FAILED test_every_run_reads_the_split_adjusted_store_from_the_study_start
FAILED test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set
```

Identical to the pre-registered baseline — no new or different failures.

```
$ python3 -m ruff check nodes.py model_zoo.py final_model.py test_nodes.py test_configs.py test_final_model.py
All checks passed!
```

## Findings

### Fix #1 (Major, cycle 1) — verified correct, and the duplicate-overrides
risk is provably impossible, not merely unlikely

`nodes.py:3536-3538` now does:

```python
selected_row = next(
    row for row in ledger.rows if dict(row["overrides"]) == selection.selected_candidate
)
return chosen, float(selected_row["score"]), ledger.to_obj(), selection.to_obj()
```

Traced the full chain to rule out silent mismatch:

- `CandidateInventory.__init__` (`kinds_search.py:538-567`) refuses any
  space dimension with duplicate values, and the grid is the Cartesian
  product of those dimensions — so `inventory.combinations` can never
  contain two identical dicts.
- `TrialLedger._reserve` (`kinds_search.py:820-831`) refuses (`ValueError`)
  a second `record()` call whose `overrides` key was already reserved or
  committed — a ledger can therefore never hold two rows with the same
  `overrides`, by construction, not by convention.
- `selection.selected_candidate` is `_json_obj(_candidate_obj(winner_row["overrides"], ...))`
  (`kinds_search.py:1074-1076`, `932-937`), and `winner_row` is itself one
  of `ledger.rows` (`kinds_search.py:1047-1051` cross-checks
  `winner_key in eligible_keys`, all keys drawn from `canonical_rows =
  ledger.rows`). Both sides of the `next()` comparison are therefore flat
  dicts of JSON scalars (space values are constrained to
  `_is_json_scalar`, no nested structures), so `dict(row["overrides"]) ==
  selection.selected_candidate` compares correctly and matches exactly one
  row.

So the "duplicate overrides across retried/tied rows" scenario the task
asked me to check cannot occur: the ledger's own admission contract
(ADR-0113, restated in its class docstring) forbids it structurally. The
`next()` call cannot silently grab the wrong row, and cannot raise
`StopIteration` either (the selected candidate is always drawn from the
ledger it is looked up in). **Fix #1 is correct and complete.**

### Fix #2 (Minor, cycle 1) — resolved, no new inaccuracy

`final_model.py:306-308` now reads `f"hpo_space: {path!r}'s
pooled-lightgbm template hpo_space is not a non-empty mapping..."`. The
branch is reached only after `matches = [t for t in templates if ...
t.get("family") == "pooled-lightgbm"]` uniquely selects `template`
(`final_model.py:285-294`), so "pooled-lightgbm template" is now an
accurate description of exactly what was matched. Grepped the full
current tree for `'lgbm'`/`"lgbm"` as a match condition or error string:
none remain outside `run-p13-pooled-model-zoo.json` (an unrelated
document `hpo_space()` never reads) and one historical-context mention in
`run-final-hpo.json`'s finalist-template `notes` ("This template's id is
'lean' (not the prior 'lgbm')") — legitimate prose explaining an id
rename, not a stale code string.

### Fix #3 (Minor, cycle 1) — resolved, and no other restatement found
anywhere in the config

`stages.select.notes` no longer names `lean-pooled-h10` (confirmed by
`json.load` + direct inspection of the field, not grep on raw text). Its
new wording — "BenchmarkSelect's existing max-mean-score mechanism
deterministically names its own winner ... and this document restates no
model name of its own" — is accurate: `BenchmarkSelect` genuinely does
select by mechanism (argmax mean score over the one pinned P16 source),
not a hardcoded name. Grepped the ENTIRE `run-final-hpo.json` file for
`lean-pooled-h10`, `pooled-h10`, and the `pooled-h` substring the
project's own test (`test_the_finalist_names_no_model_and_takes_the_selectors_winner`)
polices: zero matches anywhere in the file. No missed restatement.

### Regression check — the sole caller

`nodes.py:5833-5857` is the only call site. It destructures
`(chosen, inner_score, ledger_obj, selection_obj)`, assigns
`scan["estimator_params"] = chosen` (still `selection.selected_candidate`,
unaffected by this fix), stores `metrics["hpo_squared_error_improvement"]
= inner_score` (still a plain `float`, same type as before), and logs it.
No caller depended on `inner_score` being specifically
`selection.best_score`; the type and shape of the return value are
unchanged (a 4-tuple, same positions). **No regression.**

### Minor — the fixed function's own `Returns` docstring still names the
corrected field "best_score", the exact label that caused the original bug

`nodes.py:3453-3461`:

```
Returns
-------
tuple
    ``(chosen_params, best_score, ledger_obj, selection_obj)`` — the
    winning full ``estimator_params`` override ..., its selection score, ...
```

The fix's own inline comment (`nodes.py:3529-3535`) explains at length
that the return value must NOT be `selection.best_score`, yet the
`Returns` section's literal tuple-shape spelling still calls the second
element `best_score`. This is the same naming confusion that produced the
cycle-1 Major finding in the first place (a maintainer skimming the
docstring, not the body, would reasonably reach for
`selection.best_score` again). Purely cosmetic — no test or runtime
behavior depends on the docstring text — but it directly undermines the
"docstrings must be accurate" standard for the one function this whole
cycle exists to fix.

**Suggested fix:** rename the tuple-shape element in the docstring to
something like `selected_score` (or `inner_score`, matching the caller's
own variable name) and drop "best_score" from this docstring entirely.

### Minor — the fix shipped with no test that would have caught the
original bug, or would catch its regression

Cycle 1's report noted the shipped test
(`test_no_information_scan_hpo_evidence_builds_a_ledger_and_leaves_the_default_path_untouched`,
`test_nodes.py:1216-1344`) only asserts
`math.isfinite(evidence["metrics"]["hpo_squared_error_improvement"])`
(`test_nodes.py:1344`) — true under both the buggy and fixed code, so it
never exercised the Major finding. Commit `73cbbc6` touches no test file
at all (confirmed: `git show --stat 73cbbc6` lists only `nodes.py`,
`final_model.py`, `run-final-hpo.json`, and the retained cycle-1 memo).
The same test still only checks finiteness; nothing asserts
`evidence["metrics"]["hpo_squared_error_improvement"] ==` the selected
candidate's own row score (e.g. `next(row["score"] for row in
ledger["rows"] if row["overrides"] == winner)`), and nothing constructs a
fixture where the 1-SE winner's own score provably differs from the raw
best (the scenario the whole ADR is meant to handle). A future edit that
reverts to `selection.best_score` would pass the full suite silently.
Root `CLAUDE.md`'s duplication rule applies here in spirit: an
easy-to-reintroduce, previously-shipped bug with no runtime signal is
"unpinned" and stays a scheduled regression.

**Suggested fix:** extend the existing evidence test with an assertion
tying the reported metric to the selected row's own `score` (as done by
hand in this review and in cycle 1's reproduction), ideally over a
fixture engineered so the 1-SE winner is NOT the raw-best candidate (so
the assertion would actually fail under the old code).

## Verdict

**PASS — 0 Critical, 0 Major, 2 Minor.** All three of cycle 1's findings
are genuinely and completely fixed: the Major (inner score attributed to
the wrong candidate) is fixed correctly and the duplicate-overrides edge
case the task asked about is structurally impossible, not merely
untested; both Minors (the stale `'lgbm'` string, the config's literal
restatement of `lean-pooled-h10`) are fully resolved with no residual
inaccuracy and no missed occurrence elsewhere in the file. The focused
test suite reproduces the exact pre-registered baseline (257 passed, 12
skipped, the same 5 pre-existing environmental failures, no new or
different ones) and ruff is clean on every changed file. The two Minor
findings raised here are new, adversarial observations from this cycle —
a self-contradicting docstring label and a missing regression test for
the exact bug just fixed — neither of which affects correctness of the
shipped behavior and neither of which blocks the gate.
