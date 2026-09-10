# Skeptic review — Gate 2 deliverable 4, method/API cycle 4

**Reviewer:** GPT-5.6 Terra (`gpt-5.6-terra`). Fresh independent review of
`1979a246` against the prior D4 state `21801d7`, including the complete
`FinalRefit` API/config boundary, ADR-0114/0115/0116, prior D4 reports, and
the generic plan interface. No implementation edits were made.

## Verdict

**FAIL — 0 Critical, 1 Major, 0 Minor.** Runtime behavior is safely
fail-closed, but the correction's handoff-document alignment does not repair
an authoritative module/ADR API-description contradiction. PASS requires
zero Critical and zero Major findings.

## Major — the public module and accepted ADR describe a different API from D4

`intraday_equities/final_model.py`'s module docstring says every function is
"deliberately NOT wired as a document node in this build" and that ADR-0114
names no node class. That is false for the reviewed D4 tree:
`configs/run-final-refit.json` wires
`intraday_equities.final_model:FinalRefit`, and ADR-0116 names it as a
train-role node. The same accepted ADR's Decision still describes an enabled
future node that verifies manifests, calls the domain refit, and calls
`write_bundle` once; the actual `FinalRefit.validate_params`,
`_verified_hpo_outputs`, and `run` instead unconditionally refuse because the
driver lacks immutable run and content-derived input attestations.

The new RE-ENTRY/README/CLAUDE wording correctly says the node is present and
non-executable, but it leaves the public Python API's primary description and
the accepted D4 decision contradictory. An operator reading the module can
reasonably omit the document/plan boundary entirely, while an operator reading
the ADR can reasonably expect a refit/bundle-capable node. This is a material
contract/handoff mismatch, even though it creates no executable bypass: the
config wiring reaches unconditional plan-time refusal and `run()` cannot fit
or write a bundle.

**Required correction:** reconcile the `final_model.py` module-level contract
and ADR-0116 with the current deliberately non-executable wired node. State
that the helpers remain synthetic/direct APIs, while `FinalRefit` is a
pipeline node solely to fail closed until driver-owned immutable completed-run
and ten content-derived labelled-row identity contracts exist. Keep the ADR's
future execution description explicitly conditional on those upstream APIs.

## Confirmed controls

- The shipped config has no input wires or search knob, contains exactly the
  ten pending `h01..h10` evidence keys, and documents that filling pins cannot
  enable execution.
- Generic document validation accepts the syntactic document; planning invokes
  `FinalRefit.validate_params` and rejects it. The node's non-executable
  attestation problem is unconditional, so filled-looking pins cannot bypass
  the boundary.
- `_verified_hpo_outputs()` always rejects mutable result/node/carry sidecars;
  `run()` always rejects before winner resolution, row consumption, fitting, or
  bundle writing. The retained evidence/1-SE and `refit_heads` helpers are not
  reachable through a valid pipeline node execution.
- The correction range is documentation only. Its updated RE-ENTRY, child
  README, and CLAUDE statements match the current fail-closed behavior, but
  do not supersede the contradictory source/ADR contracts above.

## Focused verification

```text
pytest -q children/intraday_equities/tests/test_final_model.py
# 42 passed
pytest -q children/intraday_equities/tests/test_configs.py -k "final_hpo or final_refit"
# 7 passed
python -m dskit.pipeline validate configs/run-final-refit.json --adapter intraday_equities
# OK (generic document validation)
python -m dskit.pipeline plan configs/run-final-refit.json --adapter intraday_equities
# exits 1: eight expected problems, including unconditional non-executable attestation refusal
ruff check intraday_equities/final_model.py tests/test_final_model.py tests/test_configs.py
# All checks passed
git diff --check 21801d7 1979a246
git diff --check 21801d7 HEAD
# clean
```

No pipeline run, market-data read, HPO, refit, bundle write, `path.csv` edit,
or push was performed.

Co-Authored-By: GPT-5.6 Terra <noreply@openai.com>
