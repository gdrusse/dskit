# RE-ENTRY — portfolio_policy

Updated: 2026-10-28 by Claude Sonnet 5.5 (merge of `schwab-risk-inputs` into `schwab-tax-lots`).
Rewritten whole at every `/wrap`; the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

On `main`: ADR-0279 base machinery and `PositionSummary` (A0002), both review loops closed
CLEAN. Branch `schwab-tax-lots` now also carries the risk-inputs slice (merged from
`schwab-risk-inputs`, main `aa9c94e`); neither slice is lens-reviewed after the merge, and the
branch is not yet merged to `main`. One decision document runs both:

- RISK INPUTS (research A0003, ADR-0279 amendments "risk inputs"): tier 2 `schwab_daily`
  (closed daily candles as `bars` keyed `(symbol, ts)`, `ts` = the New York session date;
  today's never; live re-requests `overlap_days` 5); tier 1 `kinds_flow.DateOrderedGroupBy`
  (`max_lag_days` + `reference_date` refuse a stale or look-ahead group), `BetaHoldReturns`
  optional `betas` port, `ComputeFields` `integer`. Chain: bars -> benchmark (SPY) ->
  risk_features (252-session RMS vol, OLS beta) -> risk_day (`ts <=` decision day) ->
  risk_latest -> risk (x sqrt(252); Blume 0.67 raw + 0.33) -> vols / risk_betas ->
  `betas.betas` and `summary.volatilities`; `risks` writes `risk/<run>.jsonl`. No beta typed.
- TAX LOTS (research A0004, ADR-0279 amendments "tax lots"): tier 2 `schwab_transactions`
  (`trades` stream, `status` VALID only, `start` kept in STATE); tier 1 `tax_lots.py`
  (`TaxLots` FIFO, `LotPositions` reconciled per holding), `DecisionOutcomes.match_fields`.
  Chain: trades -> lots -> lot_positions (snapshot) -> tax (`acquired_field`) -> decide ->
  summary per lot (`lot_id`); outcomes per lot, matched per holding. `betas` still reads
  the snapshot (symbol rows); every lot of a symbol shares its wired beta and volatility.
- The decision day is in FIVE places, pinned by a test: `snapshot.where[as_of]`, `lots.as_of`,
  `tax.as_of`, `risk_day.where[ts]`, `risk_latest.reference_date`.
- README "First real run from Schwab": one runbook, three Schwab pulls (positions, trades,
  daily bars) and `mkdir -p decisions summary risk`; Limits cover both slices.

Round 1 of the tax-lots Sonnet loop is fixed (two-account tests, `order_fields` time order,
VALID status, basis-gap warning); risk-inputs round 1 likewise. No real Schwab pull yet; every
rate in the documents is a placeholder.

## Next

1. Merge-level review of `schwab-tax-lots` (two independent lenses; the join of the risk
   chain and the lot path is the new surface), then merge it to `main`.
2. Owner: the first real run from the README runbook, recorded with the memo skill. On that
   first pull check: a holding with a recent split (Schwab's close adjustment is
   undocumented), ETF / fund `asset_type` in positions and trades (`COLLECTIVE_INVESTMENT`),
   the trades census, `lot_positions` `basis_gap_max`, and `risk/<run>.jsonl` `as_of`.

## Blocked / owner decisions

- Document placeholders: market blocks, `risk_free`, tax rates, horizon, `trials`, `min_dsr`,
  `min_periods`, `redeploy_cost`, `risk_premium`, the decision day, the trades `start`
  (pinned pairs are tested).
- Lot method per account: FIFO ships; funds and some ETFs in accounts opened before October
  2024 default to average cost (not built); `basis_gap` shows a mismatch on the first run.
- `allow_incomplete` (ships false): how far back Schwab serves transactions is unverified;
  holdings older than it refuse until the owner allows the aggregate fallback or onboards a lot
  export. `lot_positions.tolerance` ships 0.0 shares (pinned).
- An INVALID (cancelled) transaction refuses the pull for good: skip it, or move `start` past
  it? Wash sales are not modelled (they show as a `basis_gap`).
- Deferred, owner may want: a default volatility for a holding without a year of bars; split
  detection.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve document (deferred).

## Read first

- `README.md` ("First real run from Schwab", Limits)
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments (last: tax lots)
- `docs/research/risk-inputs/2026-10-28-synthesis.md` (A0003)
- `docs/research/tax-lots/2026-10-28-synthesis.md` (A0004)

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not type a beta or a return into the document: all arrive wired.
- Do not change the decision day in one place only (five, pinned by a test).
