# Forecast and capital: B0–B3, C1

Owner: forecast-capital lane. Use the controlling master manifest, verified
captured-set grammar and approved V3 cap/confirmation contracts. Child code
supplies domain policy; reusable fitting, statistics and optimization belong in
the existing generic library seams. Coordinate shared child files with F5b.

## B0 — calibration fit identity

Dependencies: F1, F2, F4, F5b.
RED: `test_b0_calibration_requires_fit_identity` in the manifest's child
forecast-bundle test module. Owned seam: child `forecast_bundle.py`/`nodes.py`.

Inventory existing calibration libraries before adding one. Pin causal fit rows,
available-at label times, model identity and calibration parameters. Test valid
PIT fitting plus future labels, wrong fit identities, duplicate/reordered rows,
empty/invalid inputs and restored-state substitution. Exit requires verified
calibration identity and no fit/output effects for rejected inputs.

## B1 — independent confirmation

Dependency: B0.
RED: `test_b1_confirmation_requires_causal_pairs`; child forecast-bundle seam.

Require the approved causal, disjoint fit/test pairs and signed confirmation.
Test overlap, delayed label availability, swapped timestamps/model/calibration,
missing pairs and signature failure. Independently compute the expected small
fixture score; cover a valid signed result. Exit is a confirmation artifact whose
full causal lineage verifies, not merely a successful scorer call.

## B2 — deterministic scenarios

Dependency: B1.
RED: `test_b2_scenarios_reject_missingness_swap`; child forecast bundle/capital
adapter and the existing generic statistics owner where the master permits it.

Freeze common-instant entity matrix, weights, missingness, local-FDR inputs,
seed and generator version. Build the expected small scenario matrix outside
the production generator. Cover reordered entities/times, altered missingness or
weights, changed seeds/version, degeneracy and finite-value validation.
Exit: accepted fixtures reproduce exactly under the approved identity; each
substitution refuses before downstream cap generation.

## B3 — confirmed caps

Dependency: B2.
RED: `test_b3_cap_requires_confirmation`; same manifest-owned child seams.

Require current confirmed V3 caps, full upstream identity and timestamp.
Test stale/missing/mismatched confirmations, empty sets, and held authorization
alongside a valid nonempty result. Empty and held behavior must follow the
approved contract, not silently become permission to trade.
Exit: verified cap artifacts preserve confirmation lineage and only the
authorized downstream surface can consume them.

## C1 — verified optimization inputs

Dependencies: B3, E1, C0; do not start on B3 alone.
RED: `tests/production/test_capital.py::test_c1_mio_requires_verified_inputs`.
Own the manifest's Pyomo pack/child capital adapter; no new child-side solver.

Require B3 caps, E1 profile and C0 account identity. Use a tiny deterministic
feasible program with an independently checked solution/invariant set; cover
infeasible, stale, cross-account, missing and substituted inputs. Result is
order intent only. Assert no fills, broker orders or persistent ledger changes
during solve and reject before solver invocation when authority is invalid.
Exit: one verified intent agrees with constraints and all inputs' identities.

## Scheduling and overlap

B0–B3 run in order; C1 joins the independent E1 and C0 lanes. Calibration/scoring
TODO work in plan 09 is inventory for these packets, not another concurrent
implementation of the same formulas. Keep generic statistical capability in
the appropriate library wrapper and child policy thin. Publish only synthetic
development evidence; no market or paper/live action is authorized.

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
