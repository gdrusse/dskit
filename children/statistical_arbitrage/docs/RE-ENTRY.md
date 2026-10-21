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
`docs/research/prediction-methods/2026-10-21-better-models.md`. (4) ADR-0274
(proposed) implemented and run: the vol-scaled label meets the pre-registered
rule on ATM 20-session (R²oos +0.199 → +0.242, BH, 4/5 folds); the Kalman-OU
state (`KalmanSpreadState`, forecast aligned to the next-session label in a
second round) adds nothing; pooling across a twelve-name cohort is marginal
at 5 sessions and collapses at 20 (scale mixing). Memo:
`docs/memos/2026-10-21-tech-prediction-adr0274.md`. Two review rounds: the
first caught a Major (forecast clock) fixed and rerun; the second, two fresh
Sonnet lenses on `fbdc46db`, zero Critical/Major. (5) Data inventory
(`docs/research/data-inventory/2026-10-21-theta-and-sources.md`): nothing is
missing for the three pairs or the cohort; stock bars, VIX family, rates and
Theta Greeks are on disk; the open gaps are owner decisions (universe backfill
177 roots, about 50 h, stopped on a hard crossed-quotes alarm; 2026 sealed;
signed flow has no stream).

## Next

Owner: ratify or amend ADR-0272, ADR-0273 and ADR-0274 (all "proposed"). The
two candidates worth one separately registered confirmation each on the ATM
20-session target: the vol-scaled label (passed its rule) and the term-slope
feature; the obvious combination to register next is pooled fitting on scaled
labels across the cohort. No re-tuning on 2021–2025.
Correct the ADR-0271 stock-data prose and `source-stock-daily.json` (pinned
`close` and `dividend_amount` are already split-adjusted) before any pre-split
run.

## Blocked / owner decisions

ADR ratification; whether to acknowledge the Theta crossed-quotes alarm and
resume the universe backfill; whether to unseal 2026 for a confirmation set.
Disclosed Minor backlog: ADR-0272/0273/0274 review items (boundary and
default-deny tests, the Kalman floor/clamp/phi-bound paths untested, pin
coverage of DAG wiring, the universe document and skill knobs, an
all-zero-OI status flag in `ChainDayPositioning`); v6 audit items A3–A10;
positioning features are net sums; main's pyomo tests are an order-dependent
red in a full `tests/pipeline_libs` run.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/memos/2026-10-21-tech-prediction-adr0274.md
- docs/memos/2026-10-20-tech-prediction-ten-methods.md
- docs/research/prediction-methods/2026-10-21-better-models.md
- docs/architecture/decision-log.md, ADR-0272, ADR-0273, ADR-0274

## Do not

Do not turn a prediction result into a trade rule or re-tune on 2021–2025;
any new claim needs a new registration and the 2026 chains stay sealed. Do not
rerun a prediction document into an existing output directory (writes refuse).
Do not read the skew replay rows as a skew-structure test, and do not read the
same-day-label numbers as executable predictability.
