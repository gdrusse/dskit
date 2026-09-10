# Skeptic review — final-model replay Gate 2 deliverable 4, method/API-contract cycle 2

**Reviewer:** GPT-5.6 Terra (fresh method/API-contract skeptic)
**Review target:** `0076351` relative to `1a9309d`, including the full
ADR-0114/0115/0116 and Gate-2/D4 contract surface.
**Verdict: FAIL — 2 Critical, 1 Major.** The correction reconstructs the
pinned 24-candidate ledger and one-standard-error decision, but it does not
turn self-asserted run/data provenance into verifiable provenance. A release
must not claim a specific final-HPO execution or refit dataset merely because
mutable sidecar files and config parameters say so.

## Scope and checks

Read the complete changed `FinalRefit` implementation and config, generic
driver JSON-artifact/record/carry/result contracts, generic candidate/ledger
and bundle contracts, ADR-0114/0115/0116, the final-model/replay plan, all D4
and retained D3 method reports, and current child contracts. Tested pending
validate/plan behavior, producer/carry matching, cross-run substitution,
ledger reconstruction and selection, exact ten-head/no-search flow, cut and
identity forwarding, legacy evidence boundaries, and the real driver input
materialization contract. No market data, HPO, refit, or pipeline execution
occurred.

## Correction verified

`_winner_from_evidence()` correctly builds a `CandidateInventory` from the
hashed final-HPO document's one pooled-LightGBM recipe, requires the exact
evidence-field set, canonicalizes a complete `TrialLedger`, and recomputes the
ruled max one-standard-error/simplicity selection. Thus an incomplete,
out-of-grid, duplicate, or differently selected ledger is refused. The new
`FinalRefit` parameter shape has no HPO/search knob, requires exactly h01..h10
and one bundle call, and forwards source/cache/cut values into every head's
bundle training identity. The legacy `NoInformationScan` path is untouched.

The shipped template remains safely non-executable: `validate` succeeds as a
document check, while `plan` refuses its pending run/evidence/schema/identity
values before execution.

## Findings

### Critical 1 — record/carry matching is self-attestation, not proof of one exact completed HPO run

`FinalRefit._verified_hpo_outputs()` reads `result.json`, `carry.json`, and
`nodes/*-scan_hNN.json` as independent, mutable JSON. It checks only that
`result.json.document_hash` equals the pin, its `run_hash` is syntactically a
lowercase digest, and both copied manifest dictionaries equal the config
dictionary. Neither node record nor carry contains a run hash/document hash;
neither is hashed, signed, or otherwise bound to `result.json`; and the code
does not recompute `run_hash` from `resolved.json`/data fingerprints or verify
the directory identity. The generic driver writes exactly these independent
sidecars (`_write_node_records`, `_write_carry`, `_record_run`); it provides no
completed-run verifier.

Consequently an arbitrary content-addressed JSON payload plus forged/copied
`result.json`, carry entry, and one scan record per head meets every new check.
So can records/carry replayed from any run bearing the same document hash. The
new tests prove only that a supplied manifest must equal supplied sidecars;
they do not falsify a mutually consistent fabricated/replayed sidecar set.
This fails ADR-0116's exact-run/scan provenance requirement and C1.

Required correction: establish and verify a durable run attestation that binds
the pinned resolved/config identity, run hash/data fingerprints, each ordered
node record/output manifest, and carry (or consume a driver-owned immutable
run manifest). Test a complete mutually consistent forged/replayed sidecar
set, not only an absent or different manifest.

### Critical 2 — source/cache and full training-window identity remain unbound assertions

`refit_identity.source` and `.cache` require only a nonempty mapping with a
64-hex `sha256`; no generic resolver, input artifact, row provenance, or
computed fingerprint connects either value to `inputs`. `run()` blindly copies
those config values over the per-head identity returned by `refit_heads()`.
Thus the same fitted rows may be labelled with an arbitrary source/cache
digest, and rows assembled from different caches/sources can be released under
one claimed shared identity. The regression asserts forwarding, not that a
changed real source/cache or training input changes/rejects the bundle.

The cut is similarly only partially attested. `refit_heads()` records each
head's observed maximum timestamp, while config asserts a common
`refit_end_ms`; no input identity binds the complete input population/window.
`validate_inputs()` checks only rows earlier than `train_start_ms`; it cannot
establish that all required rows after that start through the permitted cut
were supplied. Therefore identical model bytes over different caches or
incomplete/mixed training sets can still carry the same asserted identity.
This fails C2 and ADR-0114's required data/cache/full-cut identity.

Required correction: make FinalRefit consume verified, content-derived input
identities from the owned data/cache/row-construction outputs; bind every head
row stream to them and to a canonical complete-window/cut fingerprint before
`write_bundle`. Refuse mismatches/mixed heads and test source, cache, start,
cut, and head-input substitutions alter or refuse the release.

### Major — the refit document has no wire for its required ten labelled inputs

`run-final-refit.json` declares only `pipeline.refit` and no `inputs`. The
driver constructs `inputs` solely from `spec.inputs`; a filled-in version of
this document therefore calls `FinalRefit.run(..., {})`, which refuses the
exact h01..h10 contract. Replacing PENDING values cannot make the document
execute, and there is no declared data/cache/row-construction owner from which
the preceding Critical identity can be derived. This is a configuration
contract failure, independent of the intentionally pending current values.

Required correction: define the ten immutable labelled-row input wires and
their owned verified identity producers, then add a plan-level executable
post-pin synthetic contract proving exactly ten heads reach the one refit
without HPO.

## Verification

```text
cd children/intraday_equities
PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
  /home/russell/dskit/.venv/bin/python -m dskit.pipeline validate configs/run-final-refit.json
# PASS (document validation): hash ed5709fbbbf56bc4dbdb3954ec8e81d5649b7605b936b30bd65eca18e2f3afd1

... python -m dskit.pipeline plan configs/run-final-refit.json
# exits 1, refusing the seven pending pins/schema/identity problems as intended

... python -m pytest -q tests/test_final_model.py
# 41 passed

... python -m pytest -q tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
# 269 passed, 11 skipped, 5 failed
```

The five child-suite failures are the registered unrelated baseline:
`test_run_docs_do_not_restate_the_cohort`,
`test_every_run_uses_one_local_mlflow_experiment`,
`test_the_child_installs_what_its_tracking_sinks_need`,
`test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and
`test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.

Ruff passed for the changed D4 Python files. `git diff --check 1a9309d
0076351` and the working-tree diff check passed. No full suite ran.

Because two Critical provenance/identity defects and one Major executable
configuration defect remain, this method/API cycle does not close D4.
