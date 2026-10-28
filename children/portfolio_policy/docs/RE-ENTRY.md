# RE-ENTRY — portfolio_policy

Updated: 2026-10-28 by Claude Opus 5.5 (risk inputs, branch `schwab-risk-inputs`). Rewritten
whole at every `/wrap`; the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

On `main`: ADR-0279 base machinery and `PositionSummary` (A0002), both review loops closed
CLEAN. Branch `schwab-risk-inputs` (from `689b286`; committed, NOT lens-reviewed, NOT merged)
removes the last hand inputs, research A0003 + ADR-0279 amendment "risk inputs from Schwab
daily bars (approved by owner 2026-10-28)":

- tier 2 `schwab_daily` (`SchwabDailyBarsConnector`): closed daily candles as `bars` keyed
  `(symbol, ts)`, `ts` = the New York session date of Schwab's 00:00 Central stamp (a stamp
  at or after 09:30 New York refuses); today's session never; backfill resumes after the
  cursor, live re-requests `overlap_days` (5); one token with `schwab_positions`.
- tier 1: `kinds_flow.DateOrderedGroupBy` (groupby ordered by ISO date, by import path);
  `BetaHoldReturns` optional `betas` input port (`betas_source` in its summary).
- child `run-hold-vs-redeploy.json`: bars -> benchmark (SPY) -> risk_features (252-session
  zero-mean RMS vol, 252-session OLS beta) -> risk_day (`ts <=` the decision day) ->
  risk_latest -> risk (x sqrt(252); Blume 0.67 raw + 0.33) -> vols / risk_betas ->
  `betas.betas` and `summary.volatilities`; `risks` writes `risk/<run>.jsonl`. No beta is
  typed; summary volatility and beta are non-null in the e2e.
- README "First real run from Schwab" runbook and Limits.

## Next

1. Two independent skeptic lenses on `schwab-risk-inputs` (contract: the session-date
   mapping and closed rule, the `<=` risk day, the betas port refusals), then merge.
2. Owner: the first real run from the README runbook, recorded with the memo skill. On
   that first pull check a holding with a recent split (Schwab's adjustment is
   undocumented), ETF / fund `asset_type`, and `risk/<run>.jsonl` `as_of`.

## Blocked / owner decisions

- Document placeholders: market blocks, `risk_free`, tax rates, horizon, `trials`,
  `min_dsr`, `min_periods`, `redeploy_cost`, `risk_premium` (pinned pairs are tested).
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Lot-level data (a Schwab lot CSV through `localfiles`) vs aggregate-only.
- Cadence: a weekly scheduled `pipeline run` vs a serve document (deferred).
- Deferred, owner may want: a staleness bound on the risk session; a default volatility
  for a holding without a year of bars; split detection.

## Read first

- `README.md` ("First real run from Schwab", Limits)
- `docs/research/risk-inputs/2026-10-28-synthesis.md` (A0003)
- `docs/architecture/decision-log.md` § ADR-0279 and amendments (last: risk inputs)
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not type a beta or a return into the document: all arrive wired.
- Do not change the decision day in one place only (three, pinned by a test).
