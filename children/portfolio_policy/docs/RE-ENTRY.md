# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

ADR-0279 approved 2026-10-27 with four amendments (owner: "work
autonomously", "permission to build", "build all the machinery").
Base machinery complete in dskit, all tier-1 by import path, all `pure`:
`onboarding/libs/schwab.py` (`schwab_positions`), `pipeline/liquidation.py`
(`LiquidationTax`, `HoldVsRedeploy`), `pipeline/hold_return.py`
(`MarketHoldReturn`, `BetaHoldReturns`), `pipeline/active_hurdle.py`
(`ActiveHurdle`), `pipeline/decision_outcomes.py` (`DecisionOutcomes`).
Child is JSON only: `run-hold-vs-redeploy.json` (positions → equity longs →
tax; market → betas; active_returns → hurdle; → decide →
`decisions/<run>.jsonl`) and `run-decision-outcomes.json` (that file via
localtables + a later snapshot → `outcomes/<run>.jsonl`). Two skeptic lenses
passed, then a Sonnet round 1 (5 Major) fixed: short/zero rows refuse and the
snapshot drops shorts; wired and computed rates held to one `RATE_BOUNDS`;
`hurdle.min_periods` (52) required; `market.horizon_years` pinned to the
decision's and capped by `reversion_years`; `outcomes.income_rate` (0.013 =
the dividend yield) removes income from the projected growth. Record:
`docs/memos/2026-10-27-adr-0279-base-machinery.md` and the decision log. No
real Schwab pull yet; every rate in the documents is a placeholder.

## Next

Owner: set the decision document's blocks (dividend yield, CAPE, fair
CAPE; `outcomes.income_rate` must follow the yield, a test pins it), betas,
tax rates, `trials`, `min_dsr`, `min_periods` and the snapshot day; choose
which child's backtest feeds `active`/`returns`; run the README's command
sequence for the first real snapshot + decision, then record it with the
`memo` skill.

## Blocked / owner decisions

- Lot-level data: a Schwab lot CSV through `localfiles` (then declare
  `acquired_field`) vs aggregate-only; both work, lots are more accurate.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve
  document (deferred: advisory, no order path).

## Read first

- `README.md`
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/hold-return/2026-10-27-synthesis.md`

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not paste a return into either document: both arrive wired.
