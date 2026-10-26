# RE-ENTRY — index_options

Updated: 2026-10-26 by GPT-6. Previous note: docs/archive/re-entry/index_options.md.

## Now

- Prescription is canonical ADR-0278; historical sealed labels remain ADR-0276. Prediction is ADR-0276; proposed tuning ADR-0277.
- High-level memo: docs/memos/2026-10-26-mio-high-level-status.md. It includes all 11 traded tickers and 17 dated books from the best funded MIO. Detailed history: docs/memos/2026-10-25-prescription-session-results.md.
- Best completed funded experiment: old M05 rho .03 at 28–32 decision DTE; net $6,800.05, stressed $403.80, equity $41,800.05 on $35,000 contributed, time-weighted return 13.44%, marked drawdown 65.88%. Four of ten old methods completed; six unscored. Same-year screen and base-MLP fallback limit interpretation.
- Full-DTE v11 COMPLETED: all 53 weeks/252 sessions; 17 selected, 34 timeout skips, one cash, one ineligible. 13/127 selected books filled; 256 contracts, eight tickers. Net −$7,804.40, stressed −$10,406.65, equity $27,195.60, TWR −16.29%, drawdown 62.18%; no open positions/errors.
- Independent saved-record replay passed. Parent reconciled dates, quantities, fees, P&L/stress, deposits, NAV/drawdown and source identities. All 34 skipped weeks had positive feasible incumbents with gap above $50. Native bounds trusted as approved.
- Service index-options-2019-full-dte-v11.service ended 2026-10-26 13:36:49 UTC. Peak memory9.5G/swap3.0G. All sources/tests/report remain unchanged; no optimization/full suite rerun for memo closeout.
- Runtime/report: /home/russell/.cursor/projects/home-russell-dskit/canvases/index-options-2019-condor-oracle.canvas.tsx. Raw value hash a4186a8c359d6cef1b206ce71d16bd05d346a105f4aa76bb6397e3a26773a25c. Code checkpoint82ac1ee1, approved activation565e6a28c22ce307559d1b30b8361f388b0fa0295b6fa06ce7b6da5eb0aca61b; both final code reviews C0/M0,123 focused tests/13 embedded families.
- Prediction leaders remain M51 +0.68435% and M53 +0.69034% twCRPS versus recalibrated market, positive all five regimes; M57/M64/M65 did not improve them. The trading runs use base wide-MLP64 fallback, not fitted M51.

## Next

1. Publish both memos and finish documentation wrap, then launch the explicitly requested separate Claude Code session with --model fable in its own WSL checkout of published main.
2. Fable investigates candidate methods, additional MIO work and solve speed; deliver recommendations and a minimal benchmark plan. Preserve flexible leg/quantity choices and cash. Research/proposals only; no new long runs or implementation.
3. Verify Fable's actual model/session and prompt receipt; record launch. Hourly thread follow-up finish-2019-options-run-memo stays active until delivery and confirmed launch, then pause it.
4. Audit concentration, quote artifacts, large near-zero-debit books/boxes, fill failures and timeout selection. New universe/DTE/tie-break/timeout policies prevent attribution to DTE alone. Predictive packet six under ADR-0277 stays separate.

## Blocked / owner decisions

- Owner approved offline2019 build/ADRs/execution, local native-bounds trust, $50 gap,900-second skips, both memos before wrap, and subsequent Fable investigation; do not ask again.
- Deferred minors: tuple/list cleanup compatibility and auxiliary LP thread configuration. Quote-spread rounding boundary remains documented. American assignment/broker margin/intraday fills are unmodeled.
- Holdout2024–25 stays closed; eventual January2024 then monthly testing only after rules freeze. 2026 stays sealed.

## Read first

- Root/lane AGENTS.md and docs/skills/implementation-workflow.md.
- docs/decisioning/path.csv: locked A0001–A0008.
- docs/memos/2026-10-26-mio-high-level-status.md and detailed session memo.
- docs/memos/2026-10-21-edge-methods-round5-results.md and completion addendum.
- Canonical ADR-0278 and historical review locks in docs/architecture/decision-log.md.

## Do not

- Do not open holdout/2026, pull Theta data, modify Path/Current Work, or delete pilot/report data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B, numerical threads1; one heavy study worker; C:≥30GiB.
- Preserve completed evidence and other workloads. Fable must not silently substitute another model.
