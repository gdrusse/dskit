# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Fable 5.1. Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

ADR-0279 approved 2026-10-27; amendment (autonomous) records Stage 2.
In dskit: `schwab_positions` connector (5 stub tests) and
`dskit/pipeline/liquidation.py` — `LiquidationTax` + `HoldVsRedeploy`
(9 hand-worked tests). Child: `configs/run-hold-vs-redeploy.json`
positions → tax → decide → `decisions/<run>.jsonl`, e2e test green on a
localfiles stand-in. No real Schwab pull yet; document rates are
placeholders.

Research A0001 (`docs/research/hold-return/2026-10-27-synthesis.md`):
`hold_return` should be yield + growth + CAPE reversion for the market,
scaled by beta per stock; never a trailing mean.

## Next

Owner picks the hold_return implementation (research proposes
`MarketHoldReturn` + `BetaHoldReturns` nodes and a `hold_returns` input
port on `HoldVsRedeploy`). Then the owner sets the document's rates/returns and runs the README's command
sequence for the first real snapshot + decision. Then: per-symbol
`hold_return` from price history (Stage 2 model) and `redeploy_return`
from a child's scored backtest (Stage 3 hurdle).

## Blocked / owner decisions

- Lot-level data: a Schwab lot CSV through `localfiles` (then declare
  `acquired_field`) vs aggregate-only; both work, lots are more accurate.
- Deferred tax on the hold path is ignored in this cut (favours holding).
- Cap-gains rates and holding-period rules the tax drag uses (config, not code).

## Read first

- `README.md`
- `docs/architecture/decision-log.md` § ADR-0279

## Do not

- No live orders, ever (research child; exit signals are advisory).
- No Schwab pull or model fit before ADR-0279 is ratified.
