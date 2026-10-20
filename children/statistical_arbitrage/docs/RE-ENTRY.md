# RE-ENTRY — statistical_arbitrage

## Now

ADR-0270 locks the owner-approved scope/layout; ADR-0271 is approved for implementation.
The Path records the scope; no strategy code, model fit, or backtest has run.

## Next

Inventory exact DSKit node contracts; propose the generic rolling precision and
pair-level accounting seams, then implement the five Tier 3 adapters and JSON.

## Blocked / owner decisions

Yearly refit calendar and any GPU predictive model need concrete registration before runs.

## Read first

- Root `docs/architecture/decision-log.md`, ADR-0270
- `children/index_options/configs/run-theta-chain-features-parquet.json`
- `children/index_options/configs/theta-ml-pocket-calendar.json`

## Do not

No sibling imports, option trades, real fits, or 2026 option inference.
