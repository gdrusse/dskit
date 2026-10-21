# RE-ENTRY — statistical_arbitrage

## Now

2026-10-20: candidate `626405a2` (on top of the integrated v6 evidence `b0ae8d9a`)
does two things. (1) Two independent read-only audits of the accepted v6
replay found no arithmetic or lookahead error (every hash, fit, Z, fill, fee,
hedge, dividend and metric reproduces) and two Major interpretation findings:
the "skew" books trade ATM straddles, and no book could trade before
2022-06-27 (pre-split notionals exceeded the caps), so 2020–2021 are flat by
construction; no-go unchanged. Memo:
`docs/memos/2026-10-20-v6-independent-audit.md`. (2) ADR-0272 (proposed, owner
ratification pending) prediction-only study: do date-t features predict the
5/20-session change of the ATM-IV or skew pair spreads? Three generic nodes
(`RecordsFile`, `PairSpreadHorizonRows`, `ChronologicalForecastSkill`), JSON
documents for development (2020 alpha tuning, frozen in
`configs/prediction-development-selection.json`), validation (2021–2025, run
once) and a disclosed post-hoc executable-clock run (`label_offset` 1). Result:
the lagged-spread Z carries real multi-week mean reversion of the ATM spread
(executable-clock pooled R²oos +0.20 at 20 sessions, +0.08 at 5, every fold
positive); skew predictability is one-day measurement reversal (+0.005 / +0.03);
precision and correlation edges add nothing usable. No trade rule, P&L or edge
claim. Memo: `docs/memos/2026-10-20-tech-prediction-only-study.md`. Two fresh
Sonnet skeptic lenses on the frozen candidate: zero Critical/Major; Minor/Nit
backlog recorded in the memo's handoff section (test-coverage gaps, docstring
wording, opt-in edge guards).

## Next

Owner: ratify or amend ADR-0272 (its status is "proposed"). If a follow-on is
wanted, register separately a cost-aware question about the ATM 20-session
reversion (it is a forecastability result, not a capture result). Correct the
ADR-0271 data-basis prose and `source-stock-daily.json` (pinned stock `close`
and `dividend_amount` are already split-adjusted) before any pre-split run.

## Blocked / owner decisions

ADR-0272 ratification. Nothing else blocks. Disclosed Minor backlog (audit memo
A3–A10): split-adjusted dividends vs raw-share hedges on pre-split dates,
Theta read through the derived Parquet path, no code commit inside
`evaluation.json`, unused edge sign, fragile 0.01 edge gate, "intents" wording.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/memos/2026-10-20-tech-prediction-only-study.md
- docs/memos/2026-10-20-v6-independent-audit.md
- docs/architecture/decision-log.md, ADR-0271 and ADR-0272 (with Amendment 1)
- configs/run-tech-prediction-validation.json (the frozen document)

## Do not

Do not turn the prediction result into a trade rule or re-tune on 2021–2025;
any new claim needs a new registration and the 2026 chains stay sealed. Do not
rerun a prediction document into an existing output directory (writes refuse).
Do not revive v4/v5 results or read the skew replay rows as a skew-structure test.

# RE-ENTRY — statistical_arbitrage

## Now

2026-10-20: frozen code candidate `76b4179e1719fdf6598a8b40930069be180eb55f`
has two independent clean skeptic lenses: final_v6_correctness (112 targeted
tests) and final_v6_integration (190 focused tests, Ruff clean), zero unresolved
Critical/Major. The v6 quote replay and evaluation memo are complete.
ATM precision returned -21.96%, skew precision -33.94% in 2020–2025;
all-pairs baselines lost -23.97%/-35.88%, correlation gates lost
-22.37%/-32.47%, equal-weight tech gained +282.23%, QQQ +197.61%.
No deployable edge. Memo: `docs/memos/2026-10-20-tech-phase-one-evaluation.md`.
Branch integration is pending at the time of this note.

## Next

Integrate the reviewed candidate and this editorial evidence, push main, verify
remote containment, then purge the contained task branch/worktree. Any later
research requires a new pre-registered hypothesis and untouched cohort.

## Blocked / owner decisions

No code/review blocker remains. Paper/live edge claims remain blocked by
negative net results and no untouched final test. Disclosed Minor backlog:
split crossings unmodeled; hedge cashflows displayed at close, not ex-date;
stale stock-source prose. No v6 position overlapped a known split.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/skills/skeptic-review.md
- docs/memos/2026-10-20-tech-phase-one-evaluation.md
- docs/architecture/decision-log.md, ADR-0270 and ADR-0271

## Do not

Do not revive 2026-10-19 diagnostics or v4/v5 results: causal and execution
corrections voided them. Do not infer neural epochs from solver iterations,
or describe EOD NBBO proxies as live fills.

# RE-ENTRY — statistical_arbitrage

## Now

2026-10-20: checkpoint 8b7c3479 is pushed. Two independent skeptic lenses reviewed
that exact candidate; correctness and tests/integration both blocked merge. No
code, saved structural output, or backtest data was changed by the reviews.
The prior structural signal counts are provisional because graph tuning leaks
validation-fold observations. They are not performance evidence.

## Next

Correct five Major families on a new candidate, with focused regression tests:
1. Keep opened pair exposure and P&L when a requested exit quote is missing.
2. Refit factor regression and residual scaling inside each chronological CV fold.
3. Refuse option pairs when delta hedge prerequisites or rebalance fail.
4. Search contract sizes satisfying both vega and gross-notional constraints.
5. Wire and validate backtest JSON to quote replay, annual fold/baseline/market
   metrics, and a memo. Compare any recomputed structural output with the saved
   run; if it changes, void the prior signal counts before further evaluation.
Use two fresh independent final skeptics after corrections; do not reuse the
2026-10-20 verdicts as approvals. Reviewer outputs are in this task transcript.

## Blocked / owner decisions

Merge and purge are blocked by five unresolved Major findings. No historical
quote P&L or model-versus-market evaluation exists yet.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/skills/skeptic-review.md
- docs/architecture/decision-log.md, ADR-0270 and ADR-0271
- HANDOFF-2026-10-20.md in this worktree (local, not committed)

## Do not

Do not merge this checkpoint or claim statistical arbitrage edge. Do not reuse
old structural counts after a result-changing graph correction.
