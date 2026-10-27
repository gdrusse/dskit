# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Sonnet 5.5 (position-summary round 1). Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

ADR-0279 base machinery is on `main`: tier-1 kinds by import path, all
`pure` — `liquidation.py` (`LiquidationTax`, `HoldVsRedeploy`),
`hold_return.py` (`MarketHoldReturn`, `BetaHoldReturns`),
`active_hurdle.py` (`ActiveHurdle`), `decision_outcomes.py`
(`DecisionOutcomes`); tier-2 `schwab_positions`; the child is JSON:
`run-hold-vs-redeploy.json` (positions → equity longs → tax; market →
betas; active_returns → hurdle; → decide → `decisions/<run>.jsonl`) and
`run-decision-outcomes.json` (→ `outcomes/<run>.jsonl`). Review history:
two skeptic lenses, then a Sonnet review/fix loop of four rounds (5 → 2 →
1 → 0 Major; 0 Critical throughout), round 4 CLEAN on `75fe315`: 403
dskit tests + 63 child tests green; mutation sweeps left no surviving
refusal or hardcode in the ADR-0279 modules. Record and backlog:
`docs/memos/2026-10-27-adr-0279-base-machinery.md`. No real Schwab pull
yet; every rate in the documents is a placeholder, and
`decide.risk_premium` 0.0 is one to set from the backtest's volatility.

In flight: branch `portfolio-summary` (Opus, research A0002 +
`dskit/pipeline/position_summary.py`, `PositionSummary`: weight, expected
return, beta, volatility (port only), embedded gain, tax cost, break-even
return and opportunity cost per holding, one portfolio roll-up in `metrics`;
the document's `summary` node writes `summary/<run>.jsonl`). `main`'s four
Sonnet rounds are merged into it. Its own Sonnet loop: round 1 fixed 2 Major
(loss and zero-basis rows, the break-even kinks; 304 dskit + 67 child tests
green); round 2 review is next, and it stays off `main` until the loop is clean.

## Next

Merge `portfolio-summary` after its Sonnet loop is clean. Then the owner
sets the decision document's placeholders (blocks, betas, tax rates — pinned
pairs are tested — `trials`, `min_dsr`, `min_periods`, `risk_premium`, the
snapshot day), chooses the backtest that feeds `active`/`returns`, and runs
the README sequence for the first real snapshot + decision; record it with
the `memo` skill.

## Blocked / owner decisions

- Volatility source for the summary: yahoo chart file per symbol vs Alpaca
  daily bars; 252-day window on adjusted closes proposed (then wire
  realized_vol -> filter -> ComputeFields -> keyby into `summary`).
- Lot-level data: a Schwab lot CSV through `localfiles` (then declare
  `acquired_field`) vs aggregate-only.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve
  document (deferred: advisory, no order path).

## Read first

- `README.md`
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/hold-return/2026-10-27-synthesis.md`
- `docs/research/position-summary/2026-10-27-synthesis.md`

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not paste a return into either document: both arrive wired.
