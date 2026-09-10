# Skeptic review — final-model replay Gate 2 deliverable 4, method/API-contract cycle 1

**Review target:** `f7e3e65` relative to `d0224c4`.

**Verdict: FAIL (2 Critical, 1 Major).** The PENDING document correctly refuses planning and does not fabricate pins, but the future executable contract does not bind evidence provenance or release data/cache identity. PASS requires zero Critical and zero Major.

## Scope and evidence

Read ADR-0114, ADR-0115, ADR-0116, the final-model/replay plan, complete `FinalRefit` and `refit_heads`, generic JSON-artifact/bundle contracts, config, and changed tests. I attacked pending-plan refusal, manifest validation and swapping, per-head selection/base-param binding, exact heads, feature/category forwarding, embargo/lockbox cut, no-search behavior, and bundle identity fields. No market data or refit execution was run.

## Findings

### Critical 1 — evidence manifests are not proven outputs of the pinned HPO run

`FinalRefit._winners()` verifies canonical path, size, and content hash through `resolve_json_artifact`, then accepts five payload fields, a free-text `producer_key == "scan_hNN"`, common `inventory_digest`, and a ledger self-hash. It never reads final-HPO node record, carry, or run metadata to prove each manifest was emitted by that node in this pinned execution. A separately placed canonical JSON file under `<hpo_run_dir>/artifacts/json/` can claim the matching scan key and a consistent invented ledger/selection and is accepted.

The generic resolver proves only that a caller-supplied manifest describes a caller-supplied file; it does not establish producer/run provenance. This fails ADR-0116’s requirement for ten content-addressed winner/ledger manifests from the final-HPO run and the required scan/config/source-provenance binding. New tests fabricate JSON files directly with `_json_artifact()` and monkeypatch the HPO document. None makes a real node record name the manifests, so they pass for substituted evidence.

Required correction: bind each manifest to the pinned run durable producer record, including key and output name; verify its HPO-document/run identity; test rejection of hash-valid same-directory JSON absent from `scan_hNN.hpo_ledger` and cross-run substitution.

### Critical 2 — one bundle cannot attest to required refit data/cache identity or full cuts

Plan Phase-2 item 4 requires digest coverage of cuts, data/cache identities, software versions, and model bytes. Generic `write_bundle()` includes library versions and hashes supplied fields, but `FinalRefit` supplies `training_identities` only from `refit_heads()`: `{cut_ms, seed, n_rows, winner}` per head. It supplies neither a training-start/refit-window identity nor source/cache identity. `run-final-refit.json` has no matching pinned input and no data/row-construction nodes from which to derive one.

Two refits over different source/cache content with equal row counts, max timestamps, winners, and fitted bytes are indistinguishable in required provenance fields. Those fields do not exist in the release manifest. A digest cannot cover omitted identity. The new end-to-end test replaces `refit_heads` and `write_bundle` with fakes and asserts no training identity contents.

Required correction: require/validate pinned refit data/cache identities and complete cut/window identity, forward them into each head training identity or equally hashed bundle field, and test that each change alters verified manifest/digest and absence refuses.

### Major 1 — selection is self-consistent but not contract-verified

`_winners()` accepts a selection when `ledger_digest` hashes supplied ledger and a row has `overrides == selected_candidate`. It does not verify frozen 24 candidates, a valid inventory identity for pinned HPO config, or that selected candidate is the one-standard-error result. This owner-local boundary is supposed to consume real evidence, not arbitrary internally consistent JSON. Tests use one-row ledgers with arbitrary `num_leaves`, including values outside real grid, proving contract is not exercised.

Required correction: validate generic ledger/selection against pinned 24-combination inventory and recompute or verify ruled selection; test complete-hash incomplete ledger, out-of-grid winner, and a non-one-SE selected winner.

## Confirmed controls

- Shipped config is PENDING: run directory and ten evidence entries cannot satisfy `FinalRefit.validate_params`; plan-time `ConfigError` prevents a run.
- `resolve_json_artifact` rejects traversal/noncanonical paths, malformed manifest, byte/hash mismatch, missing file, and invalid JSON.
- Node requires exactly `h01..h10`, rejects duplicate evidence hashes, checks feature order/category indices, restores base params plus own winner, delegates no-HPO refit, and writes one bundle. `refit_heads` excludes embargo and `>= 2026-03-01` rows.

## Verification

- `git diff --check f7e3e65^ f7e3e65`: clean.
- Ruff on four changed Python files: passed.
- Required narrow pytest command produced **265 passed, 11 skipped, 5 failed**. Failures are existing `test_configs.py` assumptions about pre-existing final-HPO/P16 documents: cohort/source/tracking and old pending-inventory expectation. New pending-refit assertions are not among failures, but combined requested narrow suite is not green. No full suite or real run was performed.
