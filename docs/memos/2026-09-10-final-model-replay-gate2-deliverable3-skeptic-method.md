# Skeptic review — final-model replay Gate 2 deliverable 3 (ADR-0115) method/API-contract lens

**Reviewer task:** fresh, independent skeptic review of ADR-0115 ("Real
per-lead HPO evidence inside `NoInformationScan`") as landed in commit
`e2fbd3b`, the corrected/final state after an id/family conflict found and
fixed within the same commit. Branch
`claude/phase1-recovery-seven-gates-ao4zdj`, HEAD `e2fbd3be26bbbe26ce699a41c59e43dd29ed6504`.
**Model/effort:** Claude Sonnet 5 (`claude-sonnet-5`), high.
**Dispatch:** fresh sequential reviewer, no memory of prior sessions; no
implementation edits made.

## Scope and evidence

Read in full: ADR-0115 and ADR-0114 (`docs/architecture/decision-log.md`,
including the "§11 item 1 — RULED" SE-method/simplicity section), and
`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`
§2, §4 item 2, §6 Phase 1/2 test lists. Read the complete diff:

```text
$ git show --stat e2fbd3b
 .../configs/run-final-hpo.json                     | 123 ++-----
 .../docs/decisioning/README.md                     |   4 +-
 .../docs/decisioning/actions.csv                   |   2 +
 .../intraday_equities/final_model.py               |  23 +-
 .../intraday_equities/model_zoo.py                 |   1 +
 .../intraday_equities/nodes.py                     | 316 ++++++++++++++++++-
 tests/test_configs.py                              |  82 ++++++
 tests/test_final_model.py                          |  22 +-
 tests/test_nodes.py                                | 206 ++++++++++++++
 9 files changed, 671 insertions(+), 108 deletions(-)
```

Read `nodes.py`'s new `_hpo_evidence_selection`, the `NoInformationScan.run`
diff (both the `hpo_evidence`/non-`hpo_evidence` branches side by side),
`validate_params`/`validate_outputs`, `final_model.py`'s pre-existing
(commit `a9a3112`) `run_lead_selection`/`simplicity_key`/
`cluster_scores_by_day`/`squared_error_improvement`/`EVIDENCE_FIELDS`, and
`dskit/pipeline/kinds_search.py`'s `CandidateInventory`/`TrialLedger`/
`OneStandardErrorSelector`/`SelectionRecord` contracts. Read the full
`configs/run-final-hpo.json` (via `python3 -m json.tool` and targeted
`json.load` inspection, not the notes prose) and diffed it against
`60cf723` (pre-ADR-0115).

```text
$ cd children/intraday_equities && pip install -q pyarrow lightgbm
(already present)

$ PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m pytest -q tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
5 failed, 257 passed, 12 skipped in 2.59s
FAILED tests/test_configs.py::test_run_docs_do_not_restate_the_cohort
FAILED tests/test_configs.py::test_every_run_uses_one_local_mlflow_experiment
FAILED tests/test_configs.py::test_the_child_installs_what_its_tracking_sinks_need
FAILED tests/test_configs.py::test_every_run_reads_the_split_adjusted_store_from_the_study_start
FAILED tests/test_configs.py::test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set

$ cd /home/user/dskit && python3 -m ruff check \
    children/intraday_equities/intraday_equities/nodes.py \
    children/intraday_equities/intraday_equities/model_zoo.py \
    children/intraday_equities/intraday_equities/final_model.py \
    children/intraday_equities/tests/test_nodes.py \
    children/intraday_equities/tests/test_configs.py \
    children/intraday_equities/tests/test_final_model.py
All checks passed!

$ python3 -m pytest -q tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
25 passed in 5.60s

$ git diff --check
(clean, no output)

$ cd children/intraday_equities && PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m dskit.pipeline validate configs/run-final-hpo.json --adapter intraday_equities
OK — configs/run-final-hpo.json
  name:  final-hpo
  nodes: 8  sections: splits, outputs, tracking, stages
  hash:  2db8e95a420614a91101535bb21d1ca4cd2cb6536bdb13791307754ae2805219
```

**Pre-existing-failure claim independently verified, not trusted.** Used a
detached `git worktree add /tmp/pre-adr-0115 60cf723` (pre-ADR-0115
baseline; no branch switch, per instructions) and re-ran the same five
tests there: identical 5 failures, identical `approved_inventory_sha256`
mismatch message, byte-for-byte. Confirmed unrelated to this diff; worktree
removed afterward (`git worktree remove /tmp/pre-adr-0115 --force`).

Confirmed `nodes.py` is not in `pyproject.toml`'s ruff `D`-rule
`per-file-ignores` list (only `children/_skeleton/yourproject/nodes.py` and
`dskit/pipeline/synthetic_nodes.py` are) — the clean ruff run above is a
real pass under full `D` enforcement, not a suppressed one.

## Checks that passed

1. **Backward compatibility, independently traced.** In `run()`
   (`nodes.py` ~5725-5751), when `hpo_evidence` is absent/`False`,
   `inventory` stays `None` and the code falls to the `else` branch calling
   `_hpo_combos` with identical arguments to pre-diff, then `_tune_estimator`
   is called with identical arguments at the identical two call sites
   (~5811-5859: both branches call `_scan_fold_stamped` with the exact same
   positional/keyword arguments). The only change to the shared
   non-evidence path is that `_hpo_cuts` is now computed once before the
   `if hpo_evidence` branch instead of before `_hpo_combos` — both are pure,
   side-effect-free functions, so this reordering cannot change behavior.
   `test_no_information_scan_hpo_evidence_builds_a_ledger_and_leaves_the_default_path_untouched`
   (`test_nodes.py`) is not vacuous: it runs the SAME inputs through both
   the default path (`hpo_objective="ic"`, no `hpo_evidence`) and the
   evidence path, and its first assertion (`set(baseline) ==
   {"records", "metrics"}`, i.e. no `hpo_ledger` key at all, not even
   `None`) is exactly the claim that matters.
2. **P13/P14/P15 configs never declare `hpo_evidence`.** Grepped
   `configs/run-p13-pooled-model-zoo.json`, `run-p14-recurrent-fusion-zoo.json`,
   `run-p15-temporal-fusion-zoo.json`, `run-p13-model-zoo.json` — zero
   matches.
3. **`_hpo_evidence_selection` builds one real `CandidateInventory`**
   (`nodes.py:3398-3402`, `dskit.pipeline.kinds_search.CandidateInventory`
   import, not a re-derivation) **from the declared `hpo_space`/`hpo_trials`/
   `hpo_seed`**, and fits every candidate on the identical inner
   train/holdout split `_tune_estimator` already carves (same
   `_scan_fold_stamped` call, verified above).
4. **`squared_error_improvement` is imported, never re-derived**
   (`from intraday_equities import final_model`, `nodes.py:3474`, called as
   `final_model.squared_error_improvement`) — confirmed by reading the
   import statement itself, not just the name in a docstring.
5. **Holdout rows are grouped by trading day via
   `final_model.cluster_scores_by_day`**, and `run_lead_selection` computes
   `se` via `dskit.pipeline.stats.cluster_bootstrap_t` (traced: `stats.py`
   enforces "at least two clusters" internally, matching
   `_hpo_evidence_selection`'s documented `Raises`). Every candidate is
   recorded into the ledger (`run_lead_selection` iterates
   `inventory.combinations` and calls `ledger.record` once per candidate,
   unconditionally) — none dropped, confirmed independently against the
   test's `assert len(ledger["rows"]) == 4  # every drawn candidate, none dropped`.
6. **Selection direction is correct.** `final_model.run_lead_selection`
   calls `OneStandardErrorSelector(select="max", simplicity_key=...)`.
   Squared-error improvement is `(y-mu)**2 - (y-yhat)**2` — higher is
   better — so `"max"` is the right direction; traced
   `OneStandardErrorSelector.select()` in `kinds_search.py:1273-1289`
   by hand: for `select="max"` the threshold is `best["score"] -
   best["se"]` and eligibility is `score >= threshold`, i.e. it correctly
   widens DOWNWARD from the best score for a higher-is-better metric.
   Never a raw argmin/argmax — the simplest eligible row (by
   `final_model.simplicity_key`) wins.
7. **`validate_outputs` override is correct and strictly additive**
   (`nodes.py:5566-5591`): accepts exactly `{"records", "metrics"}` or that
   plus `{"hpo_ledger"}`, refuses everything else including partial/extra
   shapes; the base `Node.validate_outputs` exact-match behavior is
   otherwise preserved as the narrower case.
8. **`hpo_ledger` is present only when evidence mode actually ran.** In
   `run()`, `hpo_ledger_obj` starts `None` and is only set inside the
   `inventory is not None` branch when `in_x.shape[0] >= 2 and
   ho_x.shape[0] >= 2`; the final `outputs["hpo_ledger"] = hpo_ledger_obj`
   assignment is itself gated on `hpo_ledger_obj is not None`
   (`nodes.py:5989-5996`) — never present-but-`None`, never
   unconditionally present.
9. **The id/family fix is correct and complete.**
   `intraday_equities.final_model.hpo_space()` now matches
   `t.get("family") == "pooled-lightgbm"` (`final_model.py:285-288`).
   Traced by hand against `configs/run-final-hpo.json`'s real,
   `json.load`-parsed structure (not the notes prose): `stages.finalist
   .params.templates` has exactly one entry, `id == "lean"`, `family ==
   "pooled-lightgbm"` — the match condition is satisfied exactly once.
   Grepped the full diff and the current tree for `id == "lgbm"`/`'lgbm'`
   as a matching condition: none remain outside `run-p13-pooled-model-zoo.json`
   (a distinct, unrelated document `hpo_space()` never reads — its
   `_DEFAULT_HPO_CONFIG` points only at `run-final-hpo.json`) and the
   `hpo_space: {path!r}'s 'lgbm' template hpo_space is not a...`
   error-message string discussed below.
10. **The "pooled-h" test fix is correct, and passes for the right
    reason.** `test_the_finalist_names_no_model_and_takes_the_selectors_winner`
    (`test_configs.py:1011-1018`) asserts `"pooled-h" not in
    json.dumps(finalist["params"]["templates"])` — scoped to the finalist
    templates block only. Independently re-verified with a standalone
    `json.dumps` of exactly that sub-object: `"pooled-h" in templates_json`
    → `False`. The two preceding assertions in the same test
    (`finalist["inputs"]["selection"]`, `select.params.select == "max"`)
    are independently true and do not raise first, so the substring
    assertion is the one actually exercised.
11. **`run_lead_selection`/`simplicity_key`/`cluster_scores_by_day`/
    `squared_error_improvement`/`EVIDENCE_FIELDS` are unchanged, pre-existing
    code from `a9a3112`** (deliverables 1/2, previously reviewed) —
    `git log --oneline -- .../final_model.py` shows only `e2fbd3b` (the
    `hpo_space()` match-condition fix) and `4f38f12` since `a9a3112`; this
    commit's `final_model.py` diff touches only `hpo_space()`.
12. **Docstrings pass ruff's `D` rules in full** (see command output
    above) and, spot-checked by eye: `_hpo_evidence_selection` and the
    class-level `hpo_objective`/`hpo_evidence` `Parameters` prose both use
    NumPy `Parameters`/`Returns`/`Raises` sections, imperative-mood summary
    lines, and contain no `>>>`. `NoInformationScan`'s pre-existing
    class-level `Examples` block (still present, unmodified) instantiates
    the class as required.

## Findings

### Major — the evidence path's reported inner score does not describe the model it actually trains

`nodes.py:3525-3529`:

```python
ledger, selection = final_model.run_lead_selection(
    inventory, evaluate, n_boot=n_boot, seed=fit_seed, alpha=alpha
)
chosen = {**base, **selection.selected_candidate}
return chosen, float(selection.best_score), ledger.to_obj(), selection.to_obj()
```

`selection.best_score` and `selection.selected_candidate` are **not the
same trial**. `SelectionRecord._build` (`kinds_search.py:1345-1353`) binds
`best_score=best["score"]` — the raw, unconstrained argmax across every
candidate — while `winner_row` (which becomes `selected_candidate`) is the
SIMPLEST candidate merely *eligible* (within one SE of that raw best),
possibly a wholly different candidate with a lower own score. This is not
a hypothetical: `final_model.py`'s own existing test proves the two can
diverge —
`test_run_lead_selection_applies_the_one_standard_error_rule`
(`test_final_model.py:346-354`) asserts `selection.best_score ==
pytest.approx(1.2)` **and** `selection.selected_candidate["num_leaves"] ==
4`, which is a different candidate than the one that scored 1.2 in that
test's fixture. I independently reproduced the general case:

```text
$ python3 -c "
from dskit.pipeline.kinds_search import CandidateInventory, TrialLedger, OneStandardErrorSelector
inv = CandidateInventory({'a':[1,2,3]}, max_candidates=10)
ledger = TrialLedger(inv, evidence_fields=('se',))
scores = {1: (9.5, 1.0), 2: (9.0, 0.1), 3: (10.0, 0.5)}
for c in inv.combinations:
    a = c['a']; score, se = scores[a]
    ledger.record(dict(c), score=score, se=se)
sel = OneStandardErrorSelector(select='max', simplicity_key=lambda r: r['overrides']['a']).select(ledger)
print(sel.best_score, sel.selected_candidate)
for row in ledger.rows:
    if row['overrides'] == sel.selected_candidate:
        print('selected candidate own score:', row['score'])
"
10.0 {'a': 1}
selected candidate own score: 9.5
```

`best_score` (10.0) belongs to candidate `a=3`; the actually selected (and
therefore actually trained — `scan["estimator_params"] = chosen` uses
`selection.selected_candidate`) candidate is `a=1`, whose own recorded
score is 9.5. `_hpo_evidence_selection` returns `10.0`, not `9.5`, as
`inner_score`. That value is then stored as
`metrics["hpo_squared_error_improvement"]` (`nodes.py:5952`) and logged as

```python
self.log.info(
    "hpo evidence: %d candidate(s), 1-SE winner "
    "squared_error_improvement=%.6g",
    len(inventory.combinations),
    inner_score,
)
```

— i.e. both the metric name and the log line explicitly claim this number
IS the "1-SE winner"'s score, but it is actually the unconstrained best
score, which in general belongs to a *different, more complex* candidate
than the one deployed. This is exactly the class of evidence
misattribution the plan and ADR-0114 §11 item 1 exist to prevent (the plan
requires per-lead diagnostics to describe the selected model; a NO-GO
diagnostic reading depends on this number meaning what it claims to mean).
It has no test coverage: the shipped test
(`test_no_information_scan_hpo_evidence_builds_a_ledger_and_leaves_the_default_path_untouched`)
only asserts `math.isfinite(evidence["metrics"]["hpo_squared_error_improvement"])`,
which passes regardless of which candidate's score is reported.

**Impact.** Does not affect which model is actually trained (`chosen` is
correctly the 1-SE-selected candidate) or the ledger's completeness/
correctness (every candidate's own true score/se is correctly recorded in
`ledger.rows`, inspectable independently of this bug). It affects only the
one summary `metrics` field and one log line, which currently overstate
the deployed candidate's inner-holdout performance whenever the 1-SE rule
picks a simpler-but-lower-scoring candidate than the raw best — the
scenario the rule exists for.

**Suggested fix.** Look up the selected candidate's own row in
`ledger.rows` (matching on `overrides`) and use that row's `score`, not
`selection.best_score`.

### Minor — a stale `'lgbm'` string survives in `hpo_space()`'s error message

`final_model.py:306`: `f"hpo_space: {path!r}'s 'lgbm' template hpo_space
is not a..."`. This branch is reached only after matching by
`family == "pooled-lightgbm"` (the `id` may now be anything, e.g. `"lean"`),
so the message names the wrong key. Cosmetic only — no test asserts this
exact string beyond substring matches that still pass — but misleading to
a future debugger.

### Minor — the "select" stage's new notes text literally names the locked candidate, contrary to the document's own stated invariant

`configs/run-final-hpo.json`'s `stages.select.notes` (new in this commit)
reads in part: `` "...now that model-family and feature-mask selection are
closed (master plan Sec.2: `lean-pooled-h10` is locked)..." ``. The
child's own `CLAUDE.md` states as an invariant: "`configs/run-final-hpo.json`
... restates no model name and refuses a selection it has no recipe for."
`test_the_finalist_names_no_model_and_takes_the_selectors_winner` only
checks `finalist["params"]["templates"]` for the `"pooled-h"` substring
(confirmed passing, see Checks item 10) and does not cover
`stages.select.notes`, so this is not caught by any test. `notes` is
excluded from the identity hash and is explanatory prose citing the
already-ratified master plan, not an operative parameter — it does not
change what `BenchmarkSelect` actually selects — so this does not block
the gate, but it is a literal restatement of the exact string the sibling
template-notes fix in this same commit was written to avoid.

## Verdict

**FAIL — 0 Critical, 1 Major, 2 Minor.** The backward-compatibility proof
holds fully (Checks 1-2), the evidence-selection machinery is correctly
wired end to end — same split, real inventory, no dropped candidates,
correct `"max"` direction, real one-SE selection via
`OneStandardErrorSelector` (Checks 3-6) — the output contract is exactly
right (Checks 7-8), and the id/family/notes fix is correct and complete
(Checks 9-10). The one Major finding is narrow and mechanically clear
(confirmed by the feature's own existing test and independently
reproduced): `_hpo_evidence_selection` reports `selection.best_score`
(the unconstrained argmax across all candidates) as the inner score of
the model it actually selects and trains (`selection.selected_candidate`),
mislabeling both a `metrics` field and a log line whenever the
one-standard-error rule does its job and picks a simpler, lower-scoring
candidate than the raw best — which is precisely the case this whole ADR
exists to handle correctly. This does not corrupt the trained model or
the ledger, but it does corrupt a diagnostic this ADR is founded on
("Every lead reports train/validation gaps... A failed diagnostic makes
the release NO-GO"), and it is a one-line, mechanically obvious fix
(read the selected row's own `score` from `ledger.rows` instead of
`selection.best_score`).
