# RE-ENTRY — index_options

Updated: 2026-10-26 by GPT-6. Previous notes: docs/archive/re-entry/index_options.md.

## Now

- Publication integration combines main 64f95755 (scenario repricer) and Fable 1fd8d7ac (MIO candidate bf39ae97 plus the 2020 memo). Runtime, tests, configs and saved results retain their parent blobs; final integration checks are pending.
- ADR-0278 MIO additions are default-off: LP group screen, per-contract and short-notional reserve floors, generic joint rows, and trailing-beta common-shock loss limits. With joint rows the LP screen restricts candidates and can exclude useful hedge groups; its certificate is for that restricted program.
- Fable's completed 2020 test: shock rows net +$4,197.20, stress +$3,212.70, TWR +12.14%, marked drawdown 37.62%; no-shock net −$8,908.05; equal-weight stock comparison +$7,354. No timeouts in either method. M51 fitted forecasts where available, fallback elsewhere; seven BA books dominate. Saved results and limitations: docs/memos/2026-10-26-shock-rows-2020-test.md and its data directory.
- Eight saved 2019 weeks with screen plus short-notional 0.2 certified in 0.4–11 seconds; v11 had five timeouts in those weeks. This changes economic choices as well as runtime. No new backtest or full suite is part of publication.
- Prior audited results stand: old funded M05 at 28–32 DTE net +$6,800.05, stress +$403.80; full-DTE v11 net −$7,804.40, 34/53 timeout skips. Both original memos and the completed canvas remain preserved. Prediction leaders remain M51/M53.
- Separate JSON diagnostic: OptionScenarioReprice reprices a synthetic book under named forward/IV/time/rate shocks; sample worst declared loss $7,492.72 at expiry selloff. No probabilities, fills or decision eligibility. Reviewed scenario candidate a2b9f0a2 had two clean final lenses.

## Next

1. Complete the bounded integration checks and publish the combined main and Fable branch under the owner's request, “Can you push all”.
2. Decide where to register the 2020 run config, whether hedge groups may bypass the screen, and how to calibrate loss_fraction and moves. Preserve free option legs, integer quantities and cash.
3. A 2019 comparison of the new MIO remains proposed; no new study is launched by this integration. Audit forecast plausibility, BA concentration and whether the shock rule mainly acts as a loss budget.
4. Use the separate scenario JSON for hypothetical diagnostics only. Its forward/IV/time/rate shocks are not the MIO's terminal beta-shock rows.

## Blocked / owner decisions

- Owner authorized publishing the completed work. The 2020 execution approval and evidence are recorded in Fable's ADR-0278 amendment; this publication opens no additional window. Locked A0001–A0008 remain binding; 2024–25 and 2026 remain sealed.
- Fable's carried Minors: beta_lookback, funding floor, minimum-pair and timeout-None test coverage; no upper shock-move bound; restricted-program certificate label. American assignment, broker margin and intraday fills are unmodeled; short-notional 0.2 is a research charge.
- Scenario Minor: very small represented shocks can leave float marks unchanged within the declared gross-value envelope.
- Prior known checks: installed-venv CLI mismatch, pre-existing E702 in test_ledger_studies.py, and the child's exact-manifest omission of old memo files. Integration results will distinguish new failures from base failures.

## Read first

- Root/lane AGENTS.md and docs/skills/implementation-workflow.md.
- docs/decisioning/path.csv and canonical ADR-0278, including the 2026-10-26 amendment.
- docs/memos/2026-10-26-shock-rows-2020-test.md and its publication addendum.
- docs/memos/2026-10-26-mio-high-level-status.md and docs/memos/2026-10-25-prescription-session-results.md.
- configs/run-option-scenario-stress.json and docs/research/scenario-stress/2026-10-26-option-book-repricing.md.

## Do not

- Do not open holdout/2026, pull Theta data, change Path/Current Work, or edit the saved v11 canvas or 2020 evidence.
- WSL2 only; project venv, numerical threads 1. Preserve other worktrees and the saved Fable conversation.
- Do not claim an unrestricted optimum for LP-screened joint rows, or treat historical research gains as promotion evidence.
