# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Fable 5.1. Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

ADR-0279 approved 2026-10-27. Stage 1 landed in dskit: `schwab_positions`
connector (`dskit/onboarding/libs/schwab.py`, shared OAuth/transport base
with the bars pack), 5 stub tests green. No real Schwab pull yet.

## Next

Owner runs the first real pull (`authorize --connector schwab_positions`,
then `acquire --mode backfill`); then Stage 1b: tax-lot asset model. Schwab's
Trader API exposes aggregate positions, not lots — lots likely need a
Schwab CSV export through `localfiles`.

## Blocked / owner decisions

- Lot-level data source: Schwab CSV export vs aggregate-only (decides Stage 1b).
- Cap-gains rates and holding-period rules the tax drag uses (config, not code).

## Read first

- `README.md`
- `docs/architecture/decision-log.md` § ADR-0279

## Do not

- No live orders, ever (research child; exit signals are advisory).
- No Schwab pull or model fit before ADR-0279 is ratified.
