# RE-ENTRY — index_options

Updated: 2026-10-26 by GPT-6. Previous note: docs/archive/re-entry/index_options.md.

## Now

- JSON scenario/stress diagnostic: `configs/run-option-scenario-stress.json` declares a synthetic same-expiry index condor and five forward/IV/time/rate scenarios. Generic `dskit.pipeline.option_pricing.OptionScenarioReprice` fully reprices each leg and reports hypothetical P&L and worst declared loss; never decision-eligible.
- Reviewed candidate `a2b9f0a2` against base `857d9a6e`. Fresh correctness and integration lenses (`/root/scenario_final_correctness_a2`, `/root/scenario_final_integration_a2`) each found zero unresolved Critical/Major. Shared Black-76 and smile bodies match base.
- Focused pricing, child config and docs checks: 174 passed, 6 skipped. Related shared callers: 638 passed, 10 skipped. WSL CLI validate/plan/run succeeded and persisted the JSON report. Ruff and diff checks passed. Full suite not run.
- Synthetic sample's largest declared loss is $7,492.72 at the expiry selloff. This is a model-mark what-if, with no probability, fill, fee, liquidity or margin claim. Research: `docs/research/scenario-stress/2026-10-26-option-book-repricing.md`.
- ADR-0278 full-DTE v11 remains a separate completed audit: net −$7,804.40, 34/53 timeout skips; best prior funded M05 net +$6,800.05 with $403.80 stress. Details: `docs/memos/2026-10-26-mio-high-level-status.md` and `docs/memos/2026-10-25-prescription-session-results.md`.

## Next

1. Edit the synthetic JSON book and named shocks for another hypothetical scenario; preserve the numeric refusal envelope and quote-currency units. Do not use the diagnostic as trading approval.
2. Continue the separately authorized Fable 5.1 MIO/methods/speed investigation from its saved session; check its current service status before acting. Do not duplicate its job.
3. Audit MIO concentration, quote artifacts, near-zero-debit books, fill failures and timeout selection before any further 2019 tuning. Predictive packet six under proposed ADR-0277 stays separate.

## Blocked / owner decisions

- Minor review backlog: extremely small represented IV/rate shocks may survive input arithmetic yet leave the final float mark unchanged. The 1e9 gross-value envelope bounds the effect to float noise; no decision eligibility is claimed.
- The child exact-manifest test omits two unrelated memo files already present at base `857d9a6e`; this predates the scenario build. No full suite rerun was requested.
- Owner decisions for ADR-0278/Fable and the locked Path A0001–A0008 remain in force. Holdout 2024–25 and 2026 are sealed.

## Read first

- Root and lane `AGENTS.md`; `docs/skills/implementation-workflow.md`.
- `children/index_options/README.md` and `configs/run-option-scenario-stress.json`.
- `docs/research/scenario-stress/2026-10-26-option-book-repricing.md`.
- `docs/memos/2026-10-26-mio-high-level-status.md` and the detailed prescription memo.
- `docs/decisioning/path.csv` and canonical ADR-0278 in `docs/architecture/decision-log.md`.

## Do not

- Do not open holdout/2026, pull Theta data, alter owner-only Path/Current Work or delete other studies' evidence.
- WSL2 only; use the project's venv. Preserve other workloads and Fable's saved session.
