# Skeptic review — final-model replay Gate 2 architecture/integration/governance lens

**Reviewer task:** fresh independent skeptic review of commit `a9a3112`
(branch `claude/phase1-recovery-seven-gates-ao4zdj`, HEAD `da5d898`),
ADR-0114 Phase 2 ("final refit and one bundle").
**Model/effort:** Claude Sonnet 5 (`claude-sonnet-5`), high.
**Lens:** architecture / integration / governance (scope discipline against
ADR-0114, tiering/purity, duplication-that-diverges, OOP pillars,
encapsulation boundary, package doc sibling sync, journal/decisioning,
scope discipline against the plan generally).
**Dispatch:** fresh retained review; no implementation edits made. A
different fresh reviewer already passed the method/API-contract lens
(`docs/memos/2026-09-09-final-model-replay-gate2-skeptic-method.md`, 0
Critical/0 Major/2 Minor) — read for context, not re-litigated here except
where explicitly asked to form an independent judgment (scope discipline
item 1 below).

## Scope and evidence

Read in full: the plan
(`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`),
ADR-0114 (`docs/architecture/decision-log.md:6184-6690`, including the full
Phase 2 section), root `CLAUDE.md`, `dskit/pipeline/CLAUDE.md`, the method
skeptic's memo, the full diff of `a9a3112`, all of the new bundle code in
`dskit/pipeline/libs/sklearn.py`, the `node.py`/`kinds_table.py`
consolidation diff, all of `final_model.py`, and both test files.

```text
$ git show --stat a9a3112
 .../docs/decisioning/README.md            |   2 +-
 .../docs/decisioning/actions.csv          |   1 +
 .../intraday_equities/final_model.py      | 644 +++++++++++++++++
 .../tests/test_final_model.py             | 558 +++++++++++++++
 dskit/pipeline/AGENTS.md                  |  20 +
 dskit/pipeline/CLAUDE.md                  |  26 +
 dskit/pipeline/README.md                  |   8 +-
 dskit/pipeline/kinds_table.py             |  21 +-
 dskit/pipeline/libs/sklearn.py            | 685 ++++++++++++++++-
 dskit/pipeline/node.py                    |  63 ++
 tests/pipeline_libs/test_sklearn.py       | 324 ++++++++++
 11 files changed, 2320 insertions(+), 32 deletions(-)

$ python3 -m pytest -q tests/pipeline_libs/test_sklearn.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
237 passed, 9 skipped, 8 warnings in 12.75s

$ python3 -m ruff check dskit/pipeline/libs/sklearn.py dskit/pipeline/node.py \
    dskit/pipeline/kinds_table.py tests/pipeline_libs/test_sklearn.py \
    children/intraday_equities/intraday_equities/final_model.py \
    children/intraday_equities/tests/test_final_model.py
All checks passed!

$ git diff --check
# clean, no output

$ cd children/intraday_equities && PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m pytest -q tests/test_final_model.py
29 passed in 1.31s
```

Additional evidence gathered for this lens:

- `git diff --stat c775ac5..HEAD -- children/intraday_equities/docs/decisioning/path.csv` — empty (untouched).
- `python -m dskit.journal render --root children/intraday_equities` reproduces `docs/decisioning/README.md` byte-for-byte (`git status --porcelain` on the file is empty after running it).
- `grep -rn "run-mean-confirmation\|run-full-system-backtest"` over the touched files — no hits; both files confirmed absent from `children/intraday_equities/configs/`; `run-final-refit.json` also confirmed absent (deliverable 3 genuinely not started).
- `grep -rn "HPO_SPACE"` across the whole repo — appears only in `final_model.py` (definition + 3 uses) and its own test file (one hand-typed literal comparison). No file reads `configs/run-final-hpo.json`'s `hpo_space` at runtime or in a test to cross-check it against the module constant.
- Traced the import graph: `kinds_table.py` and `libs/sklearn.py` both already imported from `dskit.pipeline.node` before this commit (`DEFAULT_NODE_KINDS`/`Node`, and `TrainableNode`/`reject_unknown_params` respectively), so promoting `atomic_write` there adds zero new edges to the import graph.
- Diffed the promoted function body: `node.atomic_write`'s implementation (`dskit/pipeline/node.py:173-232`) is byte-identical in logic to the deleted `kinds_table._atomic_write` (same temp-file name, same fsync/replace/unlink-on-failure structure) — a pure move, not a rewrite.
- Confirmed `dskit/pipeline/libs/sklearn.py:198-201` and `:940`/`:1113` actually import and call `atomic_write` — the graduation is consumed by the authorized deliverable, not speculative.

## Findings

### Major — `final_model.HPO_SPACE` is a second, unpinned hardcoded copy of `configs/run-final-hpo.json`'s `hpo_space`, contradicting this file's own stated duplication discipline

`final_model.py:66-80` defines:

```python
#: The real five-dimension LightGBM HPO grid (ADR-0114 §11.1 ruling),
#: copied verbatim from ``configs/run-final-hpo.json``'s pooled-lightgbm
#: template ``hpo_space`` — 324 combinations, 24 drawn by the plan-cited
#: purged inner search. This is a plain module constant, not a second
#: reader of the config, because the *values themselves* (not a file
#: layout) are what ADR-0114 locked; :func:`lean_feature_drop` below
#: takes the opposite approach for the mask specifically because the
#: plan requires that list not be hardcoded a second time.
HPO_SPACE = {
    "learning_rate": [0.003, 0.01, 0.03],
    "num_leaves": [4, 8, 16],
    "min_child_samples": [500, 1000, 2000, 4000],
    "reg_lambda": [10.0, 100.0, 1000.0],
    "reg_alpha": [0.0, 0.1, 1.0],
}
```

This is exactly the shape root `CLAUDE.md`'s "Duplication that diverges"
section names as the repo's one recurring defect class: "a value in two
places with nothing pinning them." The module's own docstring and this
comment explicitly contrast this constant with `lean_feature_drop`
(`final_model.py:164-234`), which correctly reads the P16 mask from
`configs/run-p16-feature-mask-zoo.json` at call time with no second copy
anywhere. `HPO_SPACE` gets the opposite treatment for no principled reason
tied to CLAUDE.md's actual rule — the comment's justification ("the values
... are what ADR-0114 locked," not "a file layout") is not a basis CLAUDE.md
recognizes for skipping a pinning test; the rule is unconditional: "If a
value MUST appear twice, PIN THE AGREEMENT WITH A TEST... Unpinned
duplication is a scheduled bug."

The only test touching this constant,
`test_hpo_space_is_the_real_five_dimension_grid`
(`children/intraday_equities/tests/test_final_model.py:64-75`), asserts
`HPO_SPACE` against a second hand-typed literal dict inside the test file
itself — it never opens `configs/run-final-hpo.json` to check agreement.
So there are now **three** independent transcriptions of this grid with no
mechanism tying any pair together: the config's `hpo_space` field, the
`final_model.py` module constant, and the test's literal. Confirmed by
direct comparison that the config's current pooled-lightgbm
`hpo_space` (`children/intraday_equities/configs/run-final-hpo.json` →
`stages.finalist.params.templates[0].model.hpo_space`) is presently
identical to `HPO_SPACE` — so there is no live behavioral bug today — but
nothing would catch drift the moment any one of the three changes.

This is not a hypothetical risk: ADR-0114 Phase 2's own file list names
`configs/run-final-hpo.json` as a not-yet-started deliverable in THIS SAME
phase, whose job is explicitly to "freeze one 24-combination inventory" —
i.e., to edit precisely the `hpo_space`/candidate-set content this constant
claims to mirror. Whoever implements that deferred deliverable has no test
failure to warn them that `final_model.py:74-80` also needs to change; the
repo will silently carry two candidate grids that no longer agree.

The commit message compounds this by describing the two sources
identically — "the P16 lean mask read from its one real source ...
[and] the real 24-combination LightGBM grid read from
configs/run-final-hpo.json's own hpo_space" — when the two are actually
built differently: the mask is genuinely read from the file at call time
(verified independently, see Checks below); the grid is a compile-time
literal that was manually copied from the file once. "Read from" is not an
accurate description of a hardcoded module constant, and the parallel
phrasing invites a future reader to assume both are self-updating when
only one is.

**Suggested fix:** either (a) have `final_model.py` read `hpo_space` from
`configs/run-final-hpo.json` at call time via the same `lean_feature_drop`
pattern (a `build_candidate_inventory(config_path=...)` mirror), or (b), if
a compile-time literal is intentionally preferred for this value, add a
pinning test (e.g. `test_hpo_space_agrees_with_run_final_hpo_config`) that
loads the config and asserts `HPO_SPACE == <that document's hpo_space>` —
the same test CLAUDE.md explicitly prescribes for exactly this situation.

## Checks that passed

1. **Scope discipline against ADR-0114 — `node.py`/`kinds_table.py`
   (independent judgment).** ADR-0114's Phase 2 text
   (`docs/architecture/decision-log.md:6268-6329`) never names `node.py` or
   `kinds_table.py`; the ADR's file list covers only `libs/sklearn.py`,
   `final_model.py`, and the two config files. Independently verified this
   is a legitimate, narrow, mechanical promotion rather than scope creep:
   (a) the promoted body is byte-identical logic, not a rewrite — same
   temp-file naming, fsync, replace, and unlink-on-failure structure; (b)
   the import-graph claim in the new docstring ("`node.py` sits BELOW every
   kind pack") is actually true — both `kinds_table.py` and `libs/sklearn.py`
   already imported from `node.py` before this commit, so the change adds
   zero new dependency edges; (c) the graduation is consumed, not
   speculative — `libs/sklearn.py`'s new bundle writer genuinely calls
   `atomic_write` twice (joblib dump, manifest write); (d) root `CLAUDE.md`'s
   "a function is never repeated across modules" rule is unconditional and
   would have required exactly this consolidation the moment a second
   caller needed the same logic, and ADR-0114 itself explicitly disclaims
   deciding "the joblib write path/atomicity mechanism ... implementation-time
   decisions within this scope"; (e) the pre-existing `kinds_table.py`
   `FileWrite` atomicity tests keep passing against the now-shared function,
   so behavior is proven unchanged, not merely asserted unchanged. On
   balance this is the correct call under the repo's own stronger rule, not
   an ADR-amendment-worthy expansion — consistent with, and independently
   re-derived rather than assumed from, the method reviewer's Minor note.
2. **Tiering/purity.** Read every heavy import location by hand (not just
   the test result): `dskit/pipeline/libs/sklearn.py` imports `joblib` at
   lines 673, 936, 1212, 1814 and `numpy` at line 906, all inside function
   bodies; module-top imports are stdlib + internal only (`hashlib`, `json`,
   `math`, `os`, `re`, `sys`, `dskit.pipeline.fitted`, `dskit.pipeline.node`).
   `final_model.py` imports `numpy` at line 596, inside a function body;
   module-top imports are stdlib + internal only (`json`, `os`,
   `dskit.pipeline.kinds_search`, `dskit.pipeline.libs.sklearn`,
   `dskit.pipeline.stats`). `tests/pipeline/test_purity.py` passes.
3. **Duplication — the P16 lean mask.** `lean_feature_drop`
   (`final_model.py:164-234`) reads `configs/run-p16-feature-mask-zoo.json`
   at call time and refuses on shape mismatch; no second hardcoded copy of
   the 33-name list exists in `final_model.py` itself (the test file
   carries its own independent transcription for assertion purposes, which
   is the CLAUDE.md-sanctioned "deliberate independent restatement" case —
   an assertion sourced from its subject asserts nothing).
4. **OOP pillars — `EstimatorBundle`.** A plain `__slots__` value object
   (`sklearn.py:1256-1281`) consistent with this file's existing
   `SklearnSignal`/`ColumnSubsetEstimator` conventions: constructed only by
   `load_bundle` or held directly from `write_bundle`'s return, does no
   verification itself, one `predict(head, matrix)` method with a clear
   `ValueError` on an unknown head. No `if kind ==`/`if mode ==` branching
   found anywhere in the new bundle code or in `final_model.py` (grepped
   both files; zero matches).
5. **Encapsulation boundary.** All new private helpers
   (`_bundle_head_list_problems`, `_bundle_head_params_problems`,
   `_bundle_content_hash`, `_dump_bundle_joblib`, `_refuse_bundle_clobber`,
   `_HEAD_NAME_RE`, `_BUNDLE_FORMAT`, `_BUNDLE_MANIFEST_REQUIRED`,
   `_UNHASHED_BUNDLE_FIELDS`, `_bundle_predictions`,
   `_bundle_predict_checksum`, `_bundle_surviving_features_problems`,
   `_bundle_categorical_encoding_problems`,
   `_bundle_training_identities_problems`,
   `_bundle_predict_fixture_problems`) are underscore-prefixed and absent
   from `__all__` (`sklearn.py:205-216`). `dskit/pipeline/__init__.py`
   re-exports nothing from `libs/sklearn.py` at all — not `SklearnFit`, not
   `ColumnSubsetEstimator` — confirming the pack's existing convention is
   that `libs/` exports are reached via their own module path, never
   re-exported at the package root; `write_bundle`/`load_bundle`/
   `EstimatorBundle` correctly follow that convention rather than
   introducing a new one.
6. **Package doc sibling sync.** Diffed all three of
   `dskit/pipeline/{README.md,AGENTS.md,CLAUDE.md}` against each other for
   this change: each gained a paragraph describing `write_bundle`/
   `load_bundle`/`EstimatorBundle` as a "plain value API, not a node kind,"
   the same S2-A digest-widening description, the same `predict_fixture`
   replay-on-load behavior, the same `atomic_write` promotion rationale
   including why `driver.py`'s separate copy was deliberately left alone —
   consistent in substance across all three, not just individually
   plausible. No sibling was left to drift.
7. **Journal/decisioning.** `docs/decisioning/path.csv` untouched between
   `c775ac5` and `HEAD` (empty diff). `actions.csv`'s new row (`A18856`)
   matches the file's own header schema
   (`id,category,step,executed_at,inputs,outputs,db_location,notes`) and was
   appended via the journal CLI, not hand-edited — confirmed by rerunning
   `python -m dskit.journal render --root children/intraday_equities` and
   finding zero diff against the committed `docs/decisioning/README.md`.
8. **Scope discipline against the plan generally.** No market data read,
   no real HPO run, no pipeline execution anywhere in the new code (the
   module docstring states this and the code matches — `final_model.py`'s
   functions are plain callables, not wired as document nodes).
   `configs/run-mean-confirmation.json` and
   `configs/run-full-system-backtest.json` remain absent from the repo;
   `configs/run-final-refit.json` (deliverable 3) also remains absent,
   matching the commit's own deferral claim. `git diff --stat` on `a9a3112`
   shows no `configs/*.json` changes at all.

## Verdict

**FAIL — 0 Critical, 1 Major, 0 Minor.** The bundle API's OOP shape,
encapsulation boundary, tiering/purity, doc-sibling sync, and
journal/decisioning discipline are all sound and independently verified;
the `node.py`/`kinds_table.py` touch outside ADR-0114's literal file list
is, on independent re-examination of the import graph and behavior-
preservation, the correct call rather than scope creep. The blocking issue
is `final_model.py`'s `HPO_SPACE` constant: a second, hand-copied
transcription of `configs/run-final-hpo.json`'s `hpo_space` with no test or
runtime mechanism pinning the two together, sitting directly in the path of
this same phase's still-pending deliverable 3 (editing that very config to
freeze the 24-combination inventory). This is precisely the duplication
shape root `CLAUDE.md` names as the repo's recurring defect class, and the
file's own docstring argument for treating this value differently from the
(correctly-sourced) P16 lean mask does not hold up against that rule. Fix
before closing this gate: either read the grid from the config at call time
(mirroring `lean_feature_drop`) or add a pinning test asserting `HPO_SPACE`
against the config's live content.
