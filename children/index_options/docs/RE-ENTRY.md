# RE-ENTRY — index_options

Updated: 2026-10-26 by Claude Fable 5.1. Previous note: docs/archive/re-entry/index_options.md.

## Now

- Prescription is canonical ADR-0278 (historical sealed labels ADR-0276); prediction ADR-0276; proposed tuning ADR-0277. The 2026-10-26 amendment ("build now") records the MIO speed candidate, the beta-shock control and the 2020 test.
- Reviewed candidate bf39ae97: `TransportPortfolioSolve.group_screen` "lp" (exact LP group screen), weekly-study method keys `reserve_per_contract` and `short_notional_fraction` (reserve floors), generic `joint_rows` (linear bounds across groups) and the method key `shock` (moves, loss_fraction of funding, beta_lookback; one joint row per common move on beta-shocked terminal loss). Two Sonnet lenses per slice, four Majors fixed, re-reviews PASS, Minors carried in the memo. 627 focused tests pass.
- Cause of the v11 timeouts: zero-reserve parity books (short boxes/conversions at displayed size, 90% unfillable). With screen plus short-notional 0.2 the eight rebuilt v11 weeks certify in 0.4–11 s (v11: 50–940 s, five timeouts).
- 2020 validation-year test (fitted M51 fold 2, same funding and policies, both methods replayed): no shock −$8,908 net, TWR −29.6%, drawdown −75.5%; shock rows (±5/10%, 10% of funding) +$4,197, TWR +12.1%, drawdown −37.6%; equal-weight buy-and-hold of the 17 names +$7,354, +16.6%, drawdown −36.6%. Seven BA books carry both years' totals. Memo: docs/memos/2026-10-26-shock-rows-2020-test.md; evidence docs/memos/data/2026-10-26-shock-rows-2020/.
- Earlier results stand: old M05 rho .03 at 28–32 DTE +$6,800 (stress $404); full-DTE v11 −$7,804 with 34/53 timeouts. Runtime/report canvas and v11 artifacts unchanged. Prediction leaders remain M51/M53.

## Next

1. Owner: where the 2020 run document lives in configs/ (now only the resolved params JSON in the memo data directory); whether to register it.
2. Owner: exact screen with hedges (keep every group, slow) versus the documented restriction (hedge groups without standalone value are dropped); a `screen` exemption per group is the cheap middle path.
3. A 2019 rerun with screen, short-notional 0.2 and shock rows, as v12, then both years side by side; calibrate loss_fraction and moves before, not after, reading 2020 again.
4. Carried Minors: tests for beta_lookback, `max(0, funding)`, the min-pairs threshold and timeout-None losses; an upper bound on moves; mark a restricted certificate.
5. Forecast sanity gate for directional books with implausible modeled returns (HON 69% in two months in 2019).

## Blocked / owner decisions

- Owner approved the build and the 2020 test on 2026-10-26; merge/push of the candidate was requested with the wrap.
- Holdout 2024–25 stays closed; 2026 sealed. 2021–2023 validation years unopened by this lane.
- American assignment, broker margin and intraday fills unmodeled; the 0.2 short-notional charge is a stand-in.
- Known unrelated test failure: `test_the_module_runs_as_a_command_and_reports_exit_codes` (installed venv dskit lacks option_structures); E702 at test_ledger_studies.py:1878 predates this work.

## Read first

- Root/lane AGENTS.md and docs/skills/implementation-workflow.md.
- docs/decisioning/path.csv: locked A0001–A0008 (A0003 windows: 2020–2023 validation).
- docs/memos/2026-10-26-shock-rows-2020-test.md, then docs/memos/2026-10-26-mio-high-level-status.md.
- ADR-0278 and its 2026-10-26 amendment in docs/architecture/decision-log.md.

## Do not

- Do not open holdout/2026, pull Theta data, modify Path/Current Work, or edit the v11 canvas or its artifacts.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B, numerical threads 1; one heavy study worker.
- Do not treat the lp-screened certificate as the full optimum when joint rows are present.
