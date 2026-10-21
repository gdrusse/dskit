# RE-ENTRY — statistical_arbitrage

## Now

2026-10-20 (late): three things landed or are landing on top of the integrated
v6 evidence. (1) `2665b869` on main: two read-only audits of v6 (no arithmetic
or lookahead error; two Major interpretation findings: skew books trade ATM
straddles, nothing tradable before 2022-06-27; no-go unchanged) and the
ADR-0272 prediction-only study (lagged-spread Z carries multi-week ATM-spread
reversion, next-session-label R²oos +0.20 at 20 sessions; skew is one-day
noise reversal; network edges add nothing). (2) ADR-0273 (proposed): ten more
methods on the same question, now reading Theta's EOD Greeks, vendor IV, OI,
volume, quote sizes, 30/90 term structure and stock realized vol through a new
Greeks pass (`configs/run-tech-structural-greeks.json`) and three generic nodes
(`ChainDayPositioning`, `PairPanelFeatures`, `StandardizedEstimator`). Result:
on the primary next-session label no method meets the pre-registered rule;
the term-structure slope difference is the only candidate (ATM 20-session
+0.069 R²oos over the baseline, four of five folds, DM p 0.035 before BH;
BH-significant on the secondary same-day label); vendor-Greek positioning,
realized vol, Lasso and boosting are worse. Memo:
`docs/memos/2026-10-20-tech-prediction-ten-methods.md`. Two fresh Sonnet
lenses on `e10f795b`: zero Critical/Major; Minor backlog in the memo. (3) A
research note on better model families (universe-pooled partial pooling,
Kalman-OU spread state, vol-scaled labels with CRPS, surface-factor VAR):
`docs/research/prediction-methods/2026-10-21-better-models.md`.

## Next

Owner: ratify or amend ADR-0272 and ADR-0273 (both "proposed"). If anything
is carried forward it is one single-hypothesis confirmation of the term-slope
feature on the ATM 20-session target, separately registered, no re-tuning.
Correct the ADR-0271 stock-data prose and `source-stock-daily.json` (pinned
`close` and `dividend_amount` are already split-adjusted) before any pre-split
run.

## Blocked / owner decisions

ADR ratification. Disclosed Minor backlog: ADR-0272 and ADR-0273 review
items (boundary tests, pin coverage of DAG wiring and `overwrite:false`, an
`__all__` pin, an all-zero-OI status flag in `ChainDayPositioning`); v6 audit
items A3–A10; positioning features are net sums (no strike profile, no signed
flow); main's pyomo tests are an order-dependent red in a full
`tests/pipeline_libs` run.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/memos/2026-10-20-tech-prediction-ten-methods.md
- docs/memos/2026-10-20-tech-prediction-only-study.md
- docs/memos/2026-10-20-v6-independent-audit.md
- docs/architecture/decision-log.md, ADR-0271, ADR-0272, ADR-0273

## Do not

Do not turn a prediction result into a trade rule or re-tune on 2021–2025;
any new claim needs a new registration and the 2026 chains stay sealed. Do not
rerun a prediction document into an existing output directory (writes refuse).
Do not read the skew replay rows as a skew-structure test, and do not read the
same-day-label numbers as executable predictability.
