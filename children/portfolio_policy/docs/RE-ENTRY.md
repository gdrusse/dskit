# RE-ENTRY — portfolio_policy

Updated: 2026-10-28 by Claude Sonnet 5.5 (tax lots round 1 fixes, branch `schwab-tax-lots`). Rewritten whole at every `/wrap`;
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

`PositionSummary` (research A0002, `summary/<run>.jsonl`) is on `main`; its
Sonnet loop closed CLEAN (memo).

In flight: branch `schwab-tax-lots` (Opus, research A0004, A0003 on the branch):
`schwab_transactions` (tier 2, `trades` stream), `dskit/pipeline/tax_lots.py`
(`TaxLots` FIFO lots, `LotPositions` reconciled per holding),
`DecisionOutcomes.match_fields`; the decision document runs per lot
(`trades -> lots -> lot_positions -> tax` with `acquired_field`), outcomes per
lot matched per holding. Sonnet round 1 corrected (two-account tests, `order_fields`
time order, `status` VALID, `start` in STATE, basis-gap warning, holding counts in the
scoring summary; ADR amendment "Sonnet round 1 corrections"); awaiting round 2 review;
to merge after `schwab-risk-inputs` (renumber the research row to A0004 at that merge).

## Next

Round-2 review of `schwab-tax-lots`, then merge it. The owner sets the decision
document's placeholders (blocks, betas, tax rates — pinned pairs are tested —
`trials`, `min_dsr`, `min_periods`, `risk_premium`, the snapshot day, the
trades `start`), chooses the backtest that feeds `active`/`returns`, and runs
the README sequence for the first real snapshot + decision; record it with
the `memo` skill.

## Blocked / owner decisions

- Lot method per account: FIFO ships; Schwab's default for funds and some ETFs in
  accounts opened before October 2024 is average cost (not built). `basis_gap`
  in `lot_positions` shows a mismatch on the first real run.
- `allow_incomplete` (ships false): how far back Schwab serves transactions is
  unverified; holdings older than it refuse until the owner allows the aggregate
  fallback or onboards Schwab's lot export.
- `lot_positions.tolerance` (ships 0.0 shares, pinned) for reinvestment rounding.
- An INVALID (cancelled) transaction refuses the pull for good; skip it, or move
  `start` past it? Wash sales are not modelled (they show as a `basis_gap`).
- Volatility source for the summary: yahoo chart file per symbol vs Alpaca
  daily bars; 252-day window on adjusted closes proposed (then wire
  realized_vol -> filter -> ComputeFields -> keyby into `summary`).
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve
  document (deferred: advisory, no order path).

## Read first

- `README.md`
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/hold-return/2026-10-27-synthesis.md`
- `docs/research/position-summary/2026-10-27-synthesis.md`
- `docs/research/tax-lots/2026-10-28-synthesis.md`

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not paste a return into either document: both arrive wired.
