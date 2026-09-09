# Skeptic review — final-model replay Gate 2 method/API-contract lens

**Reviewer task:** fresh independent skeptic review of commit `a9a3112`
(branch `claude/phase1-recovery-seven-gates-ao4zdj`), ADR-0114 Phase 2
("final refit and one bundle").
**Model/effort:** Claude Sonnet 5 (`claude-sonnet-5`), high.
**Dispatch:** fresh retained method/API-contract review; no implementation
edits made.

## Scope and evidence

Read in full: `children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`,
ADR-0114 (`docs/architecture/decision-log.md:6184-6690`, including the
Phase 2 section and the §11 item 1 RULED section), root `CLAUDE.md`, and
`dskit/pipeline/CLAUDE.md`. Read the full diff of `a9a3112` (`git show
--stat`), all of `dskit/pipeline/libs/sklearn.py`'s new bundle section
(lines 264-1316), the new `atomic_write` in `dskit/pipeline/node.py`
(lines 173-232), the `kinds_table.py` consolidation diff, all 645 lines of
`children/intraday_equities/intraday_equities/final_model.py`, and the
corresponding test files in full.

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
# confirmed: no configs/*.json changed — deliverable 3 (run-final-hpo.json
# edit + run-final-refit.json) genuinely not started, matching the commit
# message's own claim. path.csv untouched.

$ python3 -m pytest -q tests/pipeline_libs/test_sklearn.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
237 passed, 9 skipped, 8 warnings in 13.13s

$ python3 -m ruff check dskit/pipeline/libs/sklearn.py dskit/pipeline/node.py \
    dskit/pipeline/kinds_table.py tests/pipeline_libs/test_sklearn.py \
    children/intraday_equities/intraday_equities/final_model.py \
    children/intraday_equities/tests/test_final_model.py
All checks passed!

$ git diff --check
# clean, no output

$ cd children/intraday_equities && PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m pytest -q tests/test_final_model.py
29 passed in 1.36s
```

Independent probes run beyond the required commands:

- Recomputed `_bundle_content_hash`'s material set by hand against
  `_UNHASHED_BUNDLE_FIELDS` and the manifest actually written by
  `write_bundle` — confirmed `heads`, `head_params`, `feature_order`,
  `surviving_features`, `categorical_encoding`, `training_identities`,
  `predict_fixture`, `predict_checksum`, `format` are all hash material,
  and only `sha256`/`library_versions` are excluded (`dskit/pipeline/libs/sklearn.py:882-900`).
- Hand-verified `simplicity_key`'s sign convention against the ADR ruling:
  for the tie-break dimensions, a MORE conservative candidate (higher
  `min_child_samples`, higher `reg_lambda`, higher `reg_alpha`) produces a
  MORE negative tuple element, so ascending sort still ranks it simpler —
  matches ADR-0114 §11 item 1 exactly.
- Read `children/intraday_equities/configs/run-p16-feature-mask-zoo.json`
  directly and diffed its `"lean"` template's 33-name `drop` list,
  programmatically, against `test_final_model.py`'s independently
  transcribed `REAL_LEAN_DROP` tuple — exact match, no drift.
- Constructed a self-consistent tampered bundle (an attacker who recomputes
  `sha256` correctly after corrupting `predict_fixture`'s row widths) to
  probe `load_bundle`'s pre-hash-check manifest validation — see Findings.
- Grepped ADR-0114's full text for `node.py`, `kinds_table.py`, and
  `atomic_write` — none appear; the atomic-write consolidation these two
  files carry is not literally named in Phase 2's file list (see Findings).
- Confirmed `dskit/pipeline/libs/sklearn.py`'s module-top imports are
  stdlib/internal only (`hashlib`, `json`, `math`, `os`, `re`, `sys`,
  `dskit.pipeline.fitted`, `dskit.pipeline.node`); `joblib`/`numpy` are
  imported function-local throughout the new bundle code, preserving tier-2
  purity.
- Confirmed `ColumnSubsetEstimator`'s `feature_name(s)`/`categorical_feature`
  forwarding (ADR-0108) is completely unmodified by this diff — `final_model.py`
  reuses it via `refit_heads`'s `estimator.fit(..., feature_names=...,
  categorical_feature=...)` rather than re-deriving it, satisfying the ADR's
  "one public helper, not duplicate signature inspection in child code"
  clause by reuse.

## Findings

No Critical or Major findings survive verification. Two Minor observations
follow; neither blocks this gate.

### Minor — `write_bundle`'s `Raises` section omits the `OSError` its own test exercises

`write_bundle`'s docstring (`dskit/pipeline/libs/sklearn.py:1032-1037`)
documents only `ValueError`. But `_dump_bundle_joblib` → `atomic_write` can
raise `OSError` on a real write failure, and the suite's own
`test_bundle_write_interrupted_mid_write_leaves_no_partial_bundle`
(`tests/pipeline_libs/test_sklearn.py:2502-2519`) asserts exactly
`pytest.raises(OSError, match="disk full")` from a call to `write_bundle`.
The docstring's `Raises` section is demonstrably incomplete against the
function's actual behavior. Root `CLAUDE.md`'s docstring standard requires
`Raises` to state what the function raises; ruff's `D` rules do not check
this, so it passed lint clean. Suggested fix: add an `OSError` line noting
it propagates from the underlying atomic file write.

### Minor — `dskit/pipeline/node.py`/`kinds_table.py` are edited outside ADR-0114's literal Phase 2 file list, but for a defensible reason

ADR-0114's full text (`docs/architecture/decision-log.md:6184-6690`) never
names `node.py`, `kinds_table.py`, or `atomic_write`; Phase 2's file list
covers only `dskit/pipeline/libs/sklearn.py` (bundle + forwarding-contract
bullets), `final_model.py`, and the two config files. The commit instead
promotes `kinds_table.py`'s private `_atomic_write` to a new public
`node.atomic_write` so `sklearn.py`'s bundle writer can reuse it rather than
inlining a second copy. This is not authorized by name in the ADR, but it
is the correct call under root `CLAUDE.md`'s stronger, explicit rule ("a
function is never repeated across modules — the second copy is the bug"),
and the ADR itself disclaims deciding "the joblib write path/atomicity
mechanism ... implementation-time decisions within this scope." The
`node.py` docstring (lines 173-192) transparently documents the reasoning,
including why `driver.py`'s separate copy was deliberately left alone as
out-of-scope. Net: a technically-unlisted file touch, resolved in the
direction root `CLAUDE.md` would require anyway. Not blocking; worth a
one-line ADR addendum naming these two files for the record.

## Checks that passed

1. **Bundle digest scope (S2-A).** `_bundle_content_hash`'s `material` dict
   covers every schema-bearing field — `heads`, `head_params`,
   `feature_order`, `surviving_features`, `categorical_encoding`,
   `training_identities`, `predict_fixture`, `predict_checksum`, `format`
   — and excludes only `sha256`/`library_versions`
   (`dskit/pipeline/libs/sklearn.py:304-312`, `:882-900`). Verified by hand
   against the written manifest, not merely by reading the exclusion list.
   `tests/pipeline_libs/test_sklearn.py:2574-2611`
   (`test_every_bundle_manifest_field_is_hash_material`) parametrizes seven
   of those fields directly; `format`-tampering is covered by a separate,
   earlier-firing check (`test_bundle_load_refuses_a_bundle_that_declares_the_wrong_format`,
   correctly NOT folded into the hash-mismatch parametrize list since it is
   caught before the hash check runs). The one gap I could construct — a
   fully self-consistent tamper (attacker corrupts `predict_fixture`'s row
   shape AND recomputes a valid `sha256` over the corrupted manifest) —
   surfaces a raw `ValueError` from `numpy.asarray`'s inhomogeneous-shape
   check rather than a clean, named `load_bundle` refusal, because
   `load_bundle` does not re-run `_bundle_predict_fixture_problems` on the
   manifest before replaying it. This is still a `ValueError` (matching the
   documented contract) and requires the attacker to already know and
   correctly reproduce the hash algorithm — outside the design's own stated
   threat model ("trusted executable data ... loaded only from the locally
   produced, hash-verified release path"). Not a defect; noted for
   completeness, not raised as a finding.
2. **Atomicity.** `atomic_write` (`dskit/pipeline/node.py:173-232`) writes
   a same-directory `.tmp-<pid>` file, `fsync`s, and `os.replace`s, unlinking
   the temp file on any exception. `_dump_bundle_joblib`
   (`sklearn.py:928-940`) dumps to an in-memory `io.BytesIO` first, so the
   joblib bytes hashed by `_bundle_content_hash` are exactly the bytes
   landed on disk. The interruption point the plan requires — one file
   lands, the other does not — is exercised by a REAL interruption test,
   not a simulated one: `test_bundle_write_interrupted_mid_write_leaves_no_partial_bundle`
   (`tests/pipeline_libs/test_sklearn.py:2502-2531`) monkeypatches
   `os.replace` itself to fail on exactly the second call (the manifest),
   confirms the joblib file landed, the manifest did not, and no `.tmp-`
   leftovers survive. Independently, `atomic_write`'s own unlink-on-failure
   and old-file-preservation behavior is exercised via the pre-existing
   `kinds_table.py` `FileWrite` tests it now shares
   (`tests/pipeline/test_kinds_table_write.py:106-143`,
   `test_a_failed_write_leaves_no_temp_file_and_no_target` /
   `test_an_interrupted_write_leaves_the_old_file_intact`), which the
   consolidation makes a direct test of the promoted function. The
   joblib-succeeds/manifest-fails failure mode is verified safe at load:
   `load_bundle` refuses cleanly with "manifest ... is missing"
   (`sklearn.py:1166-1170`, tested at
   `tests/pipeline_libs/test_sklearn.py:2534-2544`); an `overwrite=True`
   retry after a stale manifest also fails safe, since the manifest's
   hash-material is checked against the CURRENT joblib bytes and a stale
   manifest referencing old bytes will mismatch.
3. **Path-escape / head-name safety.** `_HEAD_NAME_RE`
   (`sklearn.py:289`) is checked via `_bundle_head_list_problems` as the
   FIRST item accumulated into `problems`, and `write_bundle` raises before
   `_refuse_bundle_clobber` or any file I/O runs (`sklearn.py:1062-1078`).
   `test_bundle_write_refuses_a_head_name_that_could_escape_the_directory`
   (`tests/pipeline_libs/test_sklearn.py:2472-2485`) passes a real
   `"../../escape"` head name and asserts `list(tmp_path.iterdir()) == []`
   — a real pre-I/O refusal, not a lint-level assumption. (Note: nothing in
   this doorway currently builds a filesystem path FROM a head name — the
   guard is deliberately forward-looking, as the code's own comment says —
   so this is defense-in-depth, not a fix for a live path-traversal bug.)
4. **`refit_heads`'s ADR-0114 §11 item 1 compliance.** `run_lead_selection`
   calls `cluster_bootstrap_t(result["cluster_scores"], n_boot, seed,
   label=..., alpha=alpha)` (`final_model.py:511-514`) with one call
   supplying both `score` (`diagnostics["mean"]`) and `se`
   (`diagnostics["se"]`) — exactly the "one call yields both" shape the
   ruling requires. `cluster_scores_by_day` (`final_model.py:306-350`)
   groups per-decision squared-error-improvement contributions by the
   `day` key, matching "trading day as the cluster unit." `simplicity_key`
   (`final_model.py:387-424`) implements
   `(num_leaves, learning_rate, -min_child_samples, -reg_lambda,
   -reg_alpha)` verbatim; hand-verified the three negated tie-breaks sort
   MORE conservative values as simpler under ascending order (e.g.
   `min_child_samples=4000` → `-4000`, sorts before `-500`), matching the
   ADR's stated intent exactly, with the correct sign in every case.
5. **The P16 lean mask.** `lean_feature_drop` (`final_model.py:164-234`)
   reads `configs/run-p16-feature-mask-zoo.json`'s
   `stages.materialize.params.templates[id=="lean"].model.estimator_params.drop`
   directly — no second hardcoded copy in the module itself. Independently
   loaded that config and diffed its actual 33-name list against
   `test_final_model.py`'s `REAL_LEAN_DROP` transcription
   (`children/intraday_equities/tests/test_final_model.py:45-52`): exact
   match, `set(...) ^ set(...) == set()`.
6. **`refit_heads` never touches search machinery.**
   `test_refit_heads_never_calls_into_the_search_machinery`
   (`children/intraday_equities/tests/test_final_model.py:501-517`) patches
   `CandidateInventory.__init__` and `OneStandardErrorSelector.select` with
   `side_effect=AssertionError`, then calls `refit_heads` and asserts it
   succeeds — this is a real proof, not a coincidence: `refit_heads`
   (`final_model.py:535-644`) does not reference either class at all in its
   body, so any accidental future call would immediately raise the patched
   `AssertionError` and fail the test.
7. **Docstrings / ruff.** `ruff check` over all six touched files reports
   zero issues (D-rules apply to `sklearn.py`/`node.py`/`final_model.py`;
   only `kinds_table.py` carries a blanket `D` exemption in
   `pyproject.toml`, unaffected by this diff's small change there). Spot
   -checked `write_bundle`, `load_bundle`, `EstimatorBundle`, `refit_heads`,
   `simplicity_key`, and `lean_feature_drop`: imperative-mood one-line
   summaries, NumPy `Parameters`/`Returns`/`Raises`/`Examples` sections, no
   `>>>` anywhere in either file (`grep` confirmed), `::`-indented Examples
   blocks, and `# ->` markers on their own line for multi-line/dict/tuple
   outputs or trailing for single short values — consistent with the
   pre-existing house style already in this file (e.g. the pre-commit
   `# -> one prediction per row, fit on [...]` convention `EstimatorBundle`
   and `SklearnSignal` both reuse). Hand-verified every literal `# ->` value
   in `final_model.py`'s Examples against the actual function output (e.g.
   `squared_error_improvement(y=3.0, yhat=2.8, mu=2.0) # -> 0.96`,
   `simplicity_key(...) # -> (8, 0.01, -1000, -100.0, -0.1)`) — all correct.
   `EstimatorBundle`'s Examples block constructs the object via `load_bundle`
   rather than calling `EstimatorBundle(...)` directly; given the class's own
   docstring states it is "returned only by `load_bundle` ... this class
   itself does no verification," this is the more correct choice, not a
   deviation worth flagging.
8. **Package docs kept current where the ADR's file list touches them.**
   `dskit/pipeline/{README.md,CLAUDE.md,AGENTS.md}` all gained an accurate
   paragraph describing the new bundle API and the `atomic_write`
   consolidation (`git diff a9a3112~1 a9a3112 --` on those three files).
   Journal: one action recorded (`A18856`), `path.csv` untouched, matching
   ADR-0021/ADR-0056 discipline.
9. **Deliverable-3 deferral claim verified.** `git diff a9a3112~1 a9a3112
   -- children/intraday_equities/configs/` is empty; `run-final-refit.json`
   does not exist on disk. The commit message's claim that config work was
   not started is accurate.

## Verdict

**PASS — 0 Critical, 0 Major, 2 Minor.** The multi-head bundle API's digest
scope, atomicity, and path-escape refusals are all real and independently
verified (not merely asserted); `final_model.py`'s ADR-0114 §11 item 1
implementation (cluster method, cluster key, simplicity ordering with
correct signs) is exact; the P16 lean mask is read from its one real source
and matches byte-for-byte; `refit_heads` is proven never to touch search
machinery; and all required commands (focused pytest, ruff, `git diff
--check`, the child's own test run) pass clean. The two Minor findings — an
incomplete `Raises` docstring line and two files touched slightly outside
ADR-0114's literal (but admittedly non-exhaustive-by-design) Phase 2 file
list — are documentation/process nits that do not affect correctness and do
not block this gate.
