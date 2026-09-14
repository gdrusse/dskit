# Integrated acceptance: I1 and I2

Owner: model-release integration. Merge the exact accepted forecast-capital and
replay-ops pins into the integration checkout before review. Use integration
tests and the master-owned fixture/config paths; an upstream implementation
defect returns to its owning lane. Do not patch another lane inside acceptance.

## I1 — synthetic normal-pipeline proof

Dependencies: F1, F2, F5a, F5b, A4, C1, R5. Verify every current whole-slice
ReviewExit and its dependencies, not merely that similarly named files exist.
RED: `tests/integration/test_historical_json_e2e.py::test_i1_deterministic_json_e2e`.
Planned fixture: `children/intraday_equities/configs/historical_json_e2e.json`;
confirm current existence and manifest ownership first. Maintain the child's
README/AGENTS/CLAUDE when documenting the accepted fixture.

Split the matrix into focused synthetic test groups, keeping one whole I1 exit:

1. Normal JSON and identity: released model → calibration/caps → PIT forecasts
   → MIO → weekly contributions → orders/fills → one ChainLedger/AccountState
   → NAV/TWR/MWR → monitoring/holds → rolling release.
2. Admission and purity: execution-block/user-stage and poison-param denial;
   every public planner, driver, stage, walk-forward, CLI and secure-launcher
   route; missing/forged/replayed sessions and direct serve construction.
   Assert zero forbidden import, construction, environment/provider/filesystem/
   network/output effects at the specified boundary. Only explicit config reads
   allowed by the approved CLI contract may precede refusal.
3. Capture and causality: complete roster including zero-event sources, G1/G2,
   every publish → consumer freeze/hash → derived port → capture → distinct
   session → consume edge; descriptor/set equality, correction availability,
   source order and restart. Use the full master matrix, not these summaries
   as an excuse to omit cases.
4. Accounting and recovery: independent property/metamorphic expectations,
   event/effect/outbox/ACK, startup/tick/shutdown and every persisted boundary.
   Compare uninterrupted versus crash/restart release/artifact identities,
   ledger head, checkpoint, account state, outbox/cursors/events, metrics/report.
5. Compatibility: absent execution block retains the committed legacy
   canonical bytes/hash/run identity; present block changes identity. Verify
   live/replay bridge composition parity with synthetic non-emitting adapters.

Use fixture sizes and explicit commands in the packet record; no real tape,
network acquisition, HPO/refit workload or paper/live effects. A smoke test alone
does not cover I1. Exit: the full approved matrix passes with independent
expected outcomes and two clean lenses. If dependencies change materially,
repeat the affected proof and final review before accepting new pins.

## I2 — verifier chronology only

Dependency: I1.
RED: same integration module
`test_i2_verifier_rejects_reordered_adr0125_chain`.
Use F5a's pinned verifier and I1's fixture; this packet does not run a real
historical study despite the older section title.

Prove all three approved ADR-0125 chains:

- Root publications → root PIS → root-PIS-only ScopeIntent → complete G0–G7
  → root CES → PEA → private BVP/PCE → equality → CAS → ScopeAuthorization.
- Under ScopeAuthorization: StageAdmission → consumed ActionExecutionAdmission
  → per-port authorization → lifecycle receipt → CapturedAuthorizationSet
  → distinct session. Root stages reuse their bootstrap tuple; nonroots need
  their declared PUBLISHED predecessors.
- After required publications: replay PIS → CES → PEA → private BVP/PCE
  → equality → CAS → FinalReplayEntry → Final Manifest → FinalReplayAdmission
  → consumed FinalReplayAdmission → replay port/receipt/set/evidence → session.

Generate focused negative cases for missing, substituted and reordered edges,
G2-only, pre-private-plan and pre-admission access. Assert refusal before effects.
Replay PIS cannot depend on Final Manifest; final replay admission never routes
through ActionExecutionAdmission. Include one valid synthetic proof per chain.

Exit: verifier-only evidence closes I2's development acceptance. It authorizes
no other dataset/execution, retuning, release, full/lockbox backtest, paper/live
or deployment. Any desired real study is a separately approved run packet,
with actual signed gates checked at execution time.

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
