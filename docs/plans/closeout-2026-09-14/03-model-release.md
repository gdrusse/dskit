# Model release: A1–A4

Owner: model-release lane. Contract: the master plan's controlling manifest and
approved release/capture ADRs. Start only after each dependency's current whole
ReviewExit verifies. Four packets, in order; do not combine search, refit and
publication into one review candidate. Reuse existing pipeline/trainable seams.
Any significant contract change needs ADR approval before code.

## A1 — causal split identity

Dependencies: F1, F2, F4, F5b. Inventory `dskit/pipeline/node.py`,
`split_policy.py`, child `final_model.py` and existing split tests; confirm exact
paths from the manifest before assigning ownership.

- RED: `children/intraday_equities/tests/test_final_model.py::test_a1_split_requires_causal_identity`.
- Bind row provenance, causal cutoffs, split membership and embargo to the
  captured identity. Reject future/unavailable labels, reordered/substituted rows,
  different split policies and missing identity before fitting or writing outputs.
- Include valid causal membership and boundary-time fixtures. Ordinary context
  or raw paths cannot substitute for verified captured inputs.
- Exit: independently reconstructed split/row identity agrees with the accepted
  artifact; invalid families prove zero downstream effects.

## A2 — search and winner reconstruction

Dependency: A1. Own the manifest's Optuna pack and child final-model seam.

- RED: the manifest's `test_a2_search_rejects_seed_substitution`.
- Ten heads `scan_h01` through `scan_h10` share the defined search inventory.
  Bind seeds, trial ordering, causal per-day contributions, bootstrap inputs,
  one-standard-error selection and final refit rows.
- Use tiny synthetic/stub trials. Independently compute the expected selection;
  do not obtain expected winners by calling the production selector itself.
- Test seed/order/head substitution, missing or duplicated contributions,
  equal-score tie handling and positive deterministic replay.
- Exit: another verifier can reconstruct the selected winner and refit rows
  from the retained identity without running a real search.

## A3 — loadable release artifact

Dependencies: A2, F2, F4, F5b. Own the manifest's LightGBM pack, node integration
and child final-model adapter. Inventory before creating the planned pack; update
package README/AGENTS/CLAUDE together when the new public surface is authorized.

- RED: `tests/pipeline_libs/test_lightgbm_release.py::test_a3_refit_rejects_release_substitution`.
- Use the approved native LightGBM text artifact and captured binding. No
  pickle/joblib loader or configurable raw-path bypass.
- Require the opaque `CapturedModelLoad`/`VerifiedCapture` route specified by
  the approved contract. Exercise equivalent constructor, restore and load paths.
- Test correct round trip and schema/model/feature/hash substitutions, truncated
  artifacts and unavailable capture before any model publication.
- Exit: the released model restores only from the bound captured set and
  produces the pinned synthetic prediction result.

## A4 — signed publication

Dependency: A3. Own node/release and child final-model paths from the manifest.

- RED: the manifest's `test_a4_release_requires_signed_manifest`.
- Exercise `FinalRefitTrainableNode` with an approved tiny synthetic fixture:
  one fit of the selected winner, no search on load, publish one captured release.
- Cover missing/wrong signatures, winner/row/feature substitution, failed
  publication and restart. Independently assert which effects occurred and
  which did not; a mock returning “verified” is not release verification.
- Exit: signed release identity, capture receipts and restoration agree;
  failed publication exposes no accepted release.

## Handoff and limits

Copy each exact RED command from the controlling manifest after checking the
current tree.
Use the shared interpreter/cwd rules. These packets prove synthetic development
contracts; actual HPO/refit, production data and deployment retain their separate
approval gates. A4's exit feeds I1 only after the other I1 dependencies close.

## Finish: review, merge, push, purge, wrap

For each completed bounded packet, apply the [shared closeout procedure](README.md#mandatory-closeout).
Retain two clean independent lenses with zero unresolved in-scope Critical/Major,
focused evidence and visible minor dispositions. Update RE-ENTRY and the packet
record, merge the reviewed candidate with current main, push and verify remote
containment, then purge only the completed task branch using an expected-head
check; finish with wrap. A partial packet does not close its parent feature.
Keep an old source branch until its entire payload is contained in main or its
unmerged remainder has an explicit approved disposition. If blocked, preserve the
branch and evidence, do not merge or purge it, and wrap with the exact next gate.
