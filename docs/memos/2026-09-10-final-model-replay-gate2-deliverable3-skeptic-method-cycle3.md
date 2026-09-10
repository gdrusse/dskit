# Skeptic review — final-model replay Gate 2 deliverable 3, method/API-contract cycle 3

**Reviewer:** GPT-5.6 Terra (fresh method/API-contract lens)
**Review target:** correction commit `d9eb132` relative to `f67accd`, with
the complete ADR-0115 scope checked from `60cf723` through `d9eb132`.
**Verdict:** **FAIL — 0 Critical, 1 Major, 0 Minor.**

## Scope and independent evidence

Read `docs/skills/skeptic-review.md`, `docs/RE-ENTRY.md`, ADR-0115 and its
ADR-0114 §11-item-1 ruling, the prior method-cycle-1/cycle-2 and architecture
reports, and every changed implementation/config/test in the stated range.
This review did not trust the correction's prose or Sol's green claim.

The prior method Major is genuinely fixed: `_hpo_evidence_selection` now
locates the row whose `overrides` equal `selection.selected_candidate` and
reports that row's score, not `selection.best_score`. The newly-added focused
regression test constructs the divergent case, so it would fail under
`73cbbc6`'s predecessor. `hpo_evidence=False` remains the historical
two-output path; the child test checks that exact key set. The evidence path
still builds `CandidateInventory` from the declared space/trial count/seed,
uses the child-owned squared-error score and day-clustered one-SE selector,
and wraps only the opt-in ledger.

The final config declares 24 trials, seed 0, and its 324-point space. The
existing final-model test proves `build_candidate_inventory()` selects the
same deterministic 24 combinations, and the scan path invokes the same
`CandidateInventory(..., n_trials=24, seed=0)` constructor. The new generic
driver test also persists a 24-row artifact larger than carry. Thus the
claimed 24-trial inventory and default-false compatibility are supported;
the failure below is specifically the integrity contract of the new generic
persistence API.

Commands run (no full suite and no real pipeline run):

```text
/home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_driver.py -k json_artifact
1 passed, 55 deselected

cd children/intraday_equities && PYTHONPATH=/home/russell/wt/gate2-deliverable3:/home/russell/wt/gate2-deliverable3/children/intraday_equities \
  /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/test_nodes.py tests/test_configs.py tests/test_final_model.py
259 passed, 11 skipped, 5 failed
```

The five failures are the documented baseline configuration/environment
failures: `test_run_docs_do_not_restate_the_cohort`,
`test_every_run_uses_one_local_mlflow_experiment`,
`test_the_child_installs_what_its_tracking_sinks_need`,
`test_every_run_reads_the_split_adjusted_store_from_the_study_start`, and
`test_p16_feature_mask_zoo_masks_are_real_and_isolate_the_feature_set`.
They are unchanged in kind from the registered baseline. Ruff passed on all
changed implementation/test files, and `git diff --check 60cf723..d9eb132`
was clean.

## Finding

### Major — `JsonArtifact` does not provide a verified/tamper-refusing manifest contract

The correction calls the new generic seam “content-addressed, auditable” in
ADR-0115 and documents a manifest retained in node records and carry. It
persists canonical bytes and records a digest on the happy path, but supplies
neither an artifact reader/verifier nor a refusal path for a completed run.
After an artifact is edited, every supported run reader still sees only the
unchanged manifest; no pipeline function resolves its path, checks its byte
count, or re-hashes it. The new test checks the digest immediately after the
writer returns, before tampering, so it cannot establish the promised
tamper-evident behavior.

Worse, `_is_json_artifact_manifest` in `dskit/pipeline/driver.py` accepts an
ordinary output dict merely by its four keys and shallow types. It neither
requires a relative `artifacts/json/<64-hex>.json` path nor hexadecimal digest
characters, nor establishes that the dict was produced from `JsonArtifact`.
I independently ran:

```python
from dskit.pipeline.driver import _is_json_artifact_manifest, _summarize

for m in (
    {"path": "../../forged.json", "sha256": "z" * 64, "bytes": 0,
     "media_type": "application/json"},
    {"path": "/tmp/foreign.json", "sha256": "0" * 64, "bytes": 1,
     "media_type": "application/json"},
):
    print(_is_json_artifact_manifest(m), _summarize(m) == m)
```

Both cases printed `True True`. Consequently an unwrapped ordinary node output
can masquerade as a durable manifest, bypass the historical dict summary, and
be retained in records/carry as an apparently verified evidence reference.
Combined with the absence of a resolver, a post-run artifact edit is likewise
never refused. The collision check in `_persist_json_artifacts` is not a
remedy: normal reruns refuse an occupied run directory before execution, and
there is no completed-run verification operation.

This is a correctness issue for the sole purpose of the correction: a ledger
used to audit a one-SE final-model decision must be either verifiably the
recorded bytes or explicitly labelled as an unchecked convenience file. The
current public API asserts the former while implementing only a write-time
fingerprint.

**Required correction:** add one owned artifact-manifest validator/resolver
that rejects absolute/traversal/non-canonical paths, malformed digests,
missing files, byte-count drift, and digest drift before returning JSON;
ensure only driver-produced manifests receive the special record/carry
treatment (for example, via an internal tagged representation before record
serialization). Add regressions for a tampered artifact and forged manifest,
then obtain fresh independent reviews.

## Positive checks

- Canonical writer bytes are deterministic (`sort_keys=True`, compact
  separators, `allow_nan=False`, newline); digest and byte count cover those
  exact bytes. The writer uses the existing same-directory atomic writer.
- The existing-artifact collision branch compares the full expected bytes and
  refuses a mismatch if reached.
- `NoInformationScan`'s evidence ledger is emitted only after a real inner
  selection and remains absent, not `None`, in the default path.

Because the Major above remains, this cycle is not a clean skeptic pass.
