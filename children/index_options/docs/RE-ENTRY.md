# RE-ENTRY — index_options

Updated: 2026-10-25 by GPT-6. Previous note: docs/archive/re-entry/index_options.md.

## Now

- Prescription is canonical ADR-0278. Its historical ADR-0276 labels remain in sealed artifacts; upstream prediction is ADR-0276, proposed tuning ADR-0277.
- Session memo: children/index_options/docs/memos/2026-10-25-prescription-session-results.md. It covers condors, caps, free integer books, funded runs, failures, next steps and all 17 fills from the best completed account run.
- Completed funded 28–32DTE: M05 rho .03 net $6,800.05, stress $403.80, final $41,800.05 on $35,000 contributed; TWR13.44%, max drawdown65.88%. Four of ten older methods completed; others unscored. 2019 base-MLP fallback, same-year screen; no forward-performance claim.
- Latest v11 full-DTE run is active: 17 pre-2019 screened tickers, supported positive DTE≤60, M05 rho .03, $10k plus $1k every14days, weekly decisions. One profit solve, $50 absolute gap, relative0,900seconds; unresolved native timeouts skip new orders and continue existing positions/deposits.
- Code checkpoint82ac1ee1; activation lock565e6a28c22ce307559d1b30b8361f388b0fa0295b6fa06ce7b6da5eb0aca61b.123 focused tests and13 embedded families passed. Both fresh final lenses passed C0/M0/new Minor0/Nit0.
- Runtime/report: /home/russell/.cursor/projects/home-russell-dskit/canvases/index-options-2019-condor-oracle.canvas.tsx. Worktree: /home/russell/wt/index-options-harvest-20261022. No source/artifact edits while service active.
- Prediction leaders remain M51 +0.68435% and M53 +0.69034% twCRPS versus recalibrated market, positive all five regimes; M57/M64/M65 completed upstream and did not improve the leaders. See round-five memo/addendum.

## Next

1. Monitor index-options-2019-full-dte-v11.service, launched 2026-10-26 03:22:52 UTC; journal confirms initialization and START M05 W1 .03. PID2713844, invocation bc5e40558c1844f08a8097651e6d1ebd. No duplicate execution.
2. When the service ends, verify raw completion plus independent audit; append results, timeout counts, P&L/drawdown/stress and full trade history to the session memo; wrap that result addendum. Hourly thread follow-up finish-2019-options-run-memo is active; pause it after final delivery.
3. Review quote artifacts, near-zero debit/high-count books, boxes, fill shortfalls and payoff concentration before more 2019 tuning. New universe/DTE/count/timeout policies prevent attribution to DTE alone.
4. Predictive packet six under proposed ADR-0277 remains separate; no new M66–M70 work here.

## Blocked / owner decisions

- Owner approved build/ADRs, offline2019 execution, trusted local native bounds, $50 gap and900-second unresolved-week skips; do not ask again.
- Saved replay independently verifies positions/caps/values/cashflow/chronology, but coherent forged native bounds are outside this approved study contract.
- Deferred minors: tuple/list cleanup compatibility, auxiliary LP thread configuration; earlier quote-spread rounding refusal remains documented. American assignment/broker margin/intraday fills are unmodeled.
- 2024–25 stays closed; owner intends eventual January2024 then month-by-month testing only after rules freeze. 2026 stays sealed.

## Read first

- Root/lane AGENTS.md and docs/skills/implementation-workflow.md.
- docs/decisioning/path.csv: locked A0001–A0008.
- docs/memos/2026-10-25-prescription-session-results.md.
- docs/memos/2026-10-21-edge-methods-round5-results.md and completion addendum.
- Canonical ADR-0278 and historical review locks in docs/architecture/decision-log.md.

## Do not

- Do not open holdout/2026, pull Theta data, modify Path/Current Work, or delete pilot data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B, numerical threads1; one heavy study worker; C:≥30GiB.
- Keep the active worktree while the background run needs it. Runtime edits invalidate its pinned identity.
