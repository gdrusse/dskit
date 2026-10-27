# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Fable 5.1. Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

Scaffold copied from `_skeleton`; ADR-0279 proposed. No code beyond the
skeleton, no data pulled.

## Next

Owner ratifies ADR-0279, then Stage 1: a `positions` stream on the dskit
Schwab connector (tier-2 graduation) and a tax-lot asset model.

## Blocked / owner decisions

- ADR-0279 ratification.
- Cap-gains rates and holding-period rules the tax drag uses (config, not code).

## Read first

- `README.md`
- `docs/architecture/decision-log.md` § ADR-0279

## Do not

- No live orders, ever (research child; exit signals are advisory).
- No Schwab pull or model fit before ADR-0279 is ratified.
