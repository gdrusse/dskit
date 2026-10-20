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
