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
